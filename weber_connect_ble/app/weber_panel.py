#!/usr/bin/env python3
"""Weber Connect ingress panel and supervised BLE-to-MQTT runtime."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from weber_ble_pair import build_pairing_summary, load_or_create_pairing_keys, pair_once
from weber_ble_scan import scan as ble_scan
from weber_http import create_panel_server
from weber_mqtt import MqttConfig, MqttSession
from weber_persistence import read_json, write_json_atomic
from weber_runtime import (
    BridgeSettings,
    ConnectionState,
    RuntimeState,
    TaskSupervisor,
    parse_whole_number,
    retry_delay,
)
from weber_status_bridge import (
    VERSION,
    build_state,
    load_pairing_summary,
    read_status_once,
    release_ble_connection,
)

LOGGER = logging.getLogger("weber_connect_panel")

BRIDGE_MESSAGE_VERSION = 10
PAIR_MESSAGE_VERSION = 11
LISTEN_SECONDS = 8.0
BLE_TIMEOUT = 20.0
PAIR_LISTEN_SECONDS = 90.0
SCAN_SECONDS = 20.0
MAX_PROBES = 4
DEFAULT_ICON_FILE = Path(__file__).resolve().parents[1] / "icon.png"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ControllerDependencies:
    scan: Callable[..., Awaitable[dict[str, Any]]] = ble_scan
    pair: Callable[..., Awaitable[dict[str, Any]]] = pair_once
    read_status: Callable[..., Awaitable[dict[str, Any]]] = read_status_once
    release: Callable[[str], bool] = release_ble_connection
    key_loader: Callable[..., dict[str, Any]] = load_or_create_pairing_keys
    summary_builder: Callable[..., dict[str, Any]] = build_pairing_summary
    mqtt_factory: Callable[..., MqttSession] = MqttSession
    wall_time: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic
    jitter: Callable[[float, float], float] = random.uniform


class HubController:
    """Explicit runtime state machine with injected infrastructure boundaries."""

    def __init__(
        self,
        data_dir: Path,
        mqtt: dict[str, Any] | None,
        *,
        dependencies: ControllerDependencies | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.settings_file = data_dir / "settings.json"
        self.summary_file = data_dir / "pairing_summary.json"
        self.key_file = data_dir / "pairing_keys.json"
        self.status_file = data_dir / "latest_status.json"
        self.handoff_file = data_dir / "handoff.json"

        self.mqtt_config = MqttConfig.from_mapping(mqtt) if mqtt and mqtt.get("host") else None
        self.settings = BridgeSettings()
        self.summary: dict[str, Any] | None = None
        self.runtime = RuntimeState()
        self.dependencies = dependencies or ControllerDependencies()

        self._ble_lock = asyncio.Lock()
        self._cycle_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._supervisor = TaskSupervisor()
        self._mqtt_session: MqttSession | None = None
        self._auto_resume_task: asyncio.Task[Any] | None = None
        self._started = False
        self._closing = False
        self._fatal_error: BaseException | None = None
        self._fatal_event = asyncio.Event()

        self._load()

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        if self._closing:
            raise RuntimeError("controller is closing")
        self._started = True
        if self.runtime.handoff_active and self.runtime.handoff_until is not None:
            self.runtime.handoff_token += 1
            self._auto_resume_task = self._supervisor.spawn(
                "weber-auto-resume",
                self._auto_resume(self.runtime.handoff_token, self.runtime.handoff_until),
                on_error=self._operation_error,
            )
        self._supervisor.spawn(
            "weber-bridge-loop",
            self._bridge_loop(),
            on_error=self._record_fatal_error,
        )

    async def stop(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._wake.set()
        await self._supervisor.close()
        await self._close_mqtt()
        address = self.address
        if address:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.dependencies.release, address),
                    timeout=20.0,
                )
            except Exception:
                LOGGER.warning("Could not release BLE connection during shutdown", exc_info=True)

    def _record_fatal_error(self, error: BaseException) -> None:
        self._fatal_error = error
        self._fatal_event.set()
        LOGGER.critical(
            "Bridge runtime stopped unexpectedly",
            exc_info=(type(error), error, error.__traceback__),
        )

    async def wait_for_fatal_error(self) -> BaseException:
        await self._fatal_event.wait()
        assert self._fatal_error is not None
        return self._fatal_error

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if self.settings_file.exists():
            try:
                self.settings = BridgeSettings.from_mapping(read_json(self.settings_file))
            except (OSError, ValueError) as exc:
                LOGGER.warning("Could not read settings: %r", exc)
        if self.summary_file.exists():
            try:
                self.summary = load_pairing_summary(self.summary_file)
            except (OSError, ValueError) as exc:
                LOGGER.warning("Could not read pairing summary: %r", exc)
        if self.handoff_file.exists():
            try:
                handoff = read_json(self.handoff_file)
                active = handoff.get("active") is True
                until = handoff.get("until")
                if active and until is None:
                    self.runtime.handoff_active = True
                    self.runtime.handoff_until = None
                elif (
                    active
                    and isinstance(until, (int, float))
                    and until > self.dependencies.wall_time()
                ):
                    self.runtime.handoff_active = True
                    self.runtime.handoff_until = float(until)
            except (OSError, ValueError) as exc:
                LOGGER.warning("Could not read handoff state: %r", exc)
            if not self.runtime.handoff_active:
                self.handoff_file.unlink(missing_ok=True)

    def _save_settings(self) -> None:
        write_json_atomic(self.settings_file, self.settings.as_dict())

    def _save_handoff(self) -> None:
        write_json_atomic(
            self.handoff_file,
            {"active": self.runtime.handoff_active, "until": self.runtime.handoff_until},
        )

    def _clear_handoff(self) -> None:
        self.handoff_file.unlink(missing_ok=True)

    # -- derived state ------------------------------------------------------

    @property
    def address(self) -> str | None:
        if self.settings.address:
            return self.settings.address
        if self.summary:
            address = (self.summary.get("hub") or {}).get("ble_address")
            return address if isinstance(address, str) and address else None
        return None

    @property
    def paired(self) -> bool:
        return self.summary is not None and bool(self.address)

    def _can_bridge(self) -> bool:
        return (
            not self._closing
            and self.paired
            and not self.runtime.handoff_active
            and not self.runtime.scanning
            and not self.runtime.pairing
        )

    def connection_state(self) -> ConnectionState:
        if self.runtime.pairing:
            return ConnectionState.PAIRING
        if self.runtime.scanning:
            return ConnectionState.SCANNING
        if not self.paired:
            return ConnectionState.SETUP
        if self.runtime.handoff_active:
            return ConnectionState.HANDOFF
        if not self.runtime.last_read_at:
            return ConnectionState.CONNECTING
        return ConnectionState.ONLINE if self.runtime.last_read_ok else ConnectionState.OFFLINE

    def state(self) -> str:
        return self.connection_state().value

    async def snapshot(self) -> dict[str, Any]:
        remaining = None
        if self.runtime.handoff_active and self.runtime.handoff_until is not None:
            remaining = max(
                0,
                int(self.runtime.handoff_until - self.dependencies.wall_time()),
            )
        return {
            "version": VERSION,
            "state": self.state(),
            "paired": self.paired,
            "address": self.address,
            "hub": (self.summary or {}).get("hub"),
            "probes": self.runtime.last_good_state.get("probes", []),
            "probe_count": self.runtime.last_good_state.get("probe_count", 0),
            "max_probes": MAX_PROBES,
            "readings_stale": bool(self.runtime.last_good_state) and not self.runtime.last_read_ok,
            "last_read_at": self.runtime.last_read_at,
            "last_error": self.runtime.last_error,
            "setup_error": self.runtime.setup_error,
            "scanning": self.runtime.scanning,
            "pairing": self.runtime.pairing,
            "candidates": list(self.runtime.candidates),
            "retry": {
                "consecutive_failures": self.runtime.consecutive_failures,
                "next_retry_seconds": self.runtime.next_retry_seconds,
            },
            "handoff": {
                "active": self.runtime.handoff_active,
                "remaining_seconds": remaining,
                "auto_resume": self.runtime.handoff_until is not None,
            },
            "mqtt": {
                "configured": self.mqtt_config is not None,
                "published_at": self.runtime.mqtt_published_at,
                "error": self.runtime.mqtt_error,
            },
            "settings": {
                "poll_seconds": self.settings.poll_seconds,
                "handoff_minutes": self.settings.handoff_minutes,
            },
        }

    # -- actions ------------------------------------------------------------

    def _operation_error(self, error: BaseException) -> None:
        self.runtime.setup_error = str(error)
        self._wake.set()

    async def start_scan(self) -> dict[str, Any]:
        if self._closing:
            return {"ok": False, "error": "The bridge is shutting down."}
        if self.runtime.scanning or self.runtime.pairing:
            return {"ok": False, "error": "Another hub operation is already running."}
        self.runtime.scanning = True
        self.runtime.setup_error = None
        self.runtime.candidates = []
        self._wake.set()
        self._supervisor.spawn(
            "weber-scan",
            self._scan_task(),
            on_error=self._operation_error,
        )
        return {"ok": True}

    async def _scan_task(self) -> None:
        try:
            async with self._ble_lock:
                if self.address:
                    await asyncio.to_thread(self.dependencies.release, self.address)
                result = await self.dependencies.scan(
                    SCAN_SECONDS,
                    include_all=False,
                    stop_on_weber=False,
                )
            self.runtime.candidates = [
                {
                    "address": row.get("address"),
                    "name": row.get("local_name") or row.get("name") or "Weber Hub",
                    "rssi": row.get("rssi"),
                }
                for row in result.get("weber_candidates", [])
                if isinstance(row.get("address"), str) and row.get("address")
            ]
            if not self.runtime.candidates:
                self.runtime.setup_error = (
                    "No hub found. Make sure the hub is powered on, awake, and close "
                    "to your Home Assistant Bluetooth adapter, then try again."
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("Scan failed: %r", exc)
            self.runtime.setup_error = f"Bluetooth scan failed: {exc}"
        finally:
            self.runtime.scanning = False
            self._wake.set()

    async def pair(self, address: object) -> dict[str, Any]:
        if self._closing:
            return {"ok": False, "error": "The bridge is shutting down."}
        if address is not None and not isinstance(address, str):
            return {"ok": False, "error": "address must be a string or null."}
        if self.runtime.scanning or self.runtime.pairing:
            return {"ok": False, "error": "Another hub operation is already running."}
        self.runtime.pairing = True
        self.runtime.setup_error = None
        self._wake.set()
        self._supervisor.spawn(
            "weber-pair",
            self._pair_task(address.strip() if address else None),
            on_error=self._operation_error,
        )
        return {"ok": True}

    def _log_pair_events(self, result: dict[str, Any]) -> None:
        events = result.get("events") or []
        if not events:
            LOGGER.warning("Hub sent no notifications during pairing")
            return
        for event in events:
            decoded = event.get("decoded") or {}
            envelope = decoded.get("envelope") or {}
            candidate = envelope.get("body_plain_candidate") or {}
            LOGGER.warning(
                "Pairing event source=%s type=%s length=%s",
                event.get("source"),
                candidate.get("type_name") or "UNDECODED",
                event.get("length"),
            )

    async def _pair_task(self, address: str | None) -> None:
        try:
            async with self._ble_lock:
                if not address:
                    result = await self.dependencies.scan(
                        SCAN_SECONDS,
                        include_all=False,
                        stop_on_weber=True,
                    )
                    candidates = result.get("weber_candidates", [])
                    if not candidates:
                        raise RuntimeError(
                            "No hub found nearby. Wake the hub and keep it close, then try again."
                        )
                    address = candidates[0].get("address")
                if not isinstance(address, str) or not address:
                    raise RuntimeError("The selected hub has no usable Bluetooth address.")
                keys = self.dependencies.key_loader(
                    path=self.key_file,
                    display_name="Home Assistant",
                    companion_id=None,
                    companion_public_key=None,
                    reset_key=False,
                )
                args = SimpleNamespace(
                    address=address,
                    version=PAIR_MESSAGE_VERSION,
                    timeout=BLE_TIMEOUT,
                    write_without_response=False,
                    listen_seconds=PAIR_LISTEN_SECONDS,
                )
                result = await self.dependencies.pair(args, keys)
                response = result.get("pairing_response")
                if not response:
                    self._log_pair_events(result)
                    raise RuntimeError(
                        "The hub did not confirm pairing. When the hub beeps, "
                        "press the button on the hub, then try again."
                    )
                if response.get("status") != "CONFIRMED":
                    raise RuntimeError(f"The hub declined pairing ({response.get('status')}).")
                summary = self.dependencies.summary_builder(
                    address=address,
                    keys=keys,
                    pairing_response=response,
                    hub_name="Weber Connect Hub",
                    hub_serial=None,
                    hub_model="Connect Hub",
                    hub_software_revision=None,
                    hub_wifi_mac=None,
                )
                write_json_atomic(self.summary_file, summary)
                self.summary = summary
                self.settings = self.settings.with_address(address)
                self._save_settings()
                self.runtime.last_read_at = None
                self.runtime.last_read_ok = False
                self.runtime.last_error = None
                self.runtime.consecutive_failures = 0
                await self._close_mqtt()
                LOGGER.info("Paired with hub at %s", address)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("Pairing failed: %r", exc)
            self.runtime.setup_error = str(exc)
        finally:
            self.runtime.pairing = False
            self._wake.set()

    async def handoff(self, minutes: int | None) -> dict[str, Any]:
        if not self.paired:
            return {"ok": False, "error": "No hub is paired yet."}
        try:
            parsed_minutes = (
                self.settings.handoff_minutes
                if minutes is None
                else parse_whole_number(minutes, "minutes")
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        parsed_minutes = max(0, min(240, parsed_minutes))

        self._cancel_auto_resume()
        self.runtime.handoff_active = True
        self.runtime.handoff_until = None
        self.runtime.handoff_token += 1
        token = self.runtime.handoff_token
        self._wake.set()
        try:
            async with self._ble_lock:
                address = self.address
                if address:
                    await asyncio.to_thread(self.dependencies.release, address)
        except Exception as exc:
            self.runtime.handoff_active = False
            self._clear_handoff()
            return {"ok": False, "error": f"Could not release the hub: {exc}"}

        if parsed_minutes > 0:
            self.runtime.handoff_until = self.dependencies.wall_time() + parsed_minutes * 60
            self._save_handoff()
            self._auto_resume_task = self._supervisor.spawn(
                "weber-auto-resume",
                self._auto_resume(token, self.runtime.handoff_until),
                on_error=self._operation_error,
            )
            LOGGER.info("Hub handed off to the phone app; auto-resume in %s minutes", parsed_minutes)
        else:
            self._save_handoff()
            LOGGER.info("Hub handed off to the phone app until manually resumed")
        return {"ok": True}

    async def _auto_resume(self, token: int, until: float) -> None:
        try:
            await asyncio.sleep(max(0.0, until - self.dependencies.wall_time()))
            if self.runtime.handoff_active and self.runtime.handoff_token == token:
                LOGGER.info("Handoff window ended; reconnecting to hub")
                self.runtime.handoff_active = False
                self.runtime.handoff_until = None
                self._clear_handoff()
                self._wake.set()
        finally:
            if self._auto_resume_task is asyncio.current_task():
                self._auto_resume_task = None

    def _cancel_auto_resume(self) -> None:
        task, self._auto_resume_task = self._auto_resume_task, None
        if task is not None and not task.done():
            task.cancel()

    async def resume(self) -> dict[str, Any]:
        self._cancel_auto_resume()
        self.runtime.handoff_active = False
        self.runtime.handoff_until = None
        self.runtime.handoff_token += 1
        self._clear_handoff()
        self._wake.set()
        return {"ok": True}

    async def forget(self) -> dict[str, Any]:
        """Forget the hub locally while retaining the reusable companion keypair."""
        if self.runtime.scanning or self.runtime.pairing:
            return {"ok": False, "error": "Wait for the current hub operation to finish."}
        async with self._cycle_lock:
            self._cancel_auto_resume()
            self.runtime.handoff_active = False
            self.runtime.handoff_until = None
            self.runtime.handoff_token += 1
            await self._close_mqtt()
            async with self._ble_lock:
                address = self.address
                if address:
                    await asyncio.to_thread(self.dependencies.release, address)
                self.summary = None
                self.settings = self.settings.with_address(None)
                self._save_settings()
            self.runtime.last_good_state = {}
            self.runtime.last_read_at = None
            self.runtime.last_read_ok = False
            self.runtime.last_error = None
            self.runtime.consecutive_failures = 0
            self.runtime.next_retry_seconds = None
            self.runtime.candidates = []
            for path in (self.summary_file, self.status_file, self.handoff_file):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    LOGGER.warning("Could not remove %s: %r", path, exc)
        self._wake.set()
        return {"ok": True}

    async def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            updated = self.settings.updated(payload)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.settings = updated
        self._save_settings()
        self._wake.set()
        return {"ok": True, "settings": self.settings.as_dict()}

    # -- bridge and MQTT ----------------------------------------------------

    async def _record_read_failure(self, message: str) -> bool:
        self.runtime.last_read_ok = False
        self.runtime.last_error = message
        self.runtime.last_read_at = utc_now()
        self.runtime.consecutive_failures += 1
        if self.summary and self.address:
            disconnected = build_state(
                self.summary,
                {},
                self.address,
                connected=False,
                max_probes=MAX_PROBES,
            )
            write_json_atomic(self.status_file, disconnected)
            await self._publish(disconnected)
        return False

    async def _read_cycle(self) -> bool:
        async with self._cycle_lock:
            return await self._read_cycle_once()

    async def _read_cycle_once(self) -> bool:
        async with self._ble_lock:
            if not self._can_bridge() or not self.summary or not self.address:
                return False
            try:
                result = await self.dependencies.read_status(
                    address=self.address,
                    companion_id=self.summary["companion_id"],
                    version=BRIDGE_MESSAGE_VERSION,
                    listen_seconds=LISTEN_SECONDS,
                    timeout=BLE_TIMEOUT,
                    write_without_response=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("Read failed: %r", exc)
                return await self._record_read_failure(f"Could not reach the hub: {exc}")

        latest = result.get("latest_status")
        if not latest:
            return await self._record_read_failure("Connected, but the hub sent no probe status.")

        self.runtime.last_read_ok = True
        self.runtime.last_error = None
        self.runtime.last_read_at = utc_now()
        self.runtime.consecutive_failures = 0
        state = build_state(
            self.summary,
            latest,
            self.address,
            connected=bool(result.get("connected")),
            max_probes=MAX_PROBES,
        )
        self.runtime.last_good_state = state
        write_json_atomic(self.status_file, state)
        await self._publish(state)
        return True

    async def _publish(self, state: dict[str, Any]) -> None:
        if self.mqtt_config is None or self.summary is None:
            return
        try:
            if self._mqtt_session is None:
                self._mqtt_session = self.dependencies.mqtt_factory(
                    self.mqtt_config,
                    self.summary,
                    max_probes=MAX_PROBES,
                )
            await self._mqtt_session.publish(state, self.settings.poll_seconds)
            self.runtime.mqtt_published_at = utc_now()
            self.runtime.mqtt_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.runtime.mqtt_error = str(exc)
            LOGGER.error("MQTT publish failed: %r", exc)

    async def _close_mqtt(self) -> None:
        session, self._mqtt_session = self._mqtt_session, None
        if session is None:
            return
        try:
            await asyncio.wait_for(session.close(), timeout=20.0)
        except Exception:
            LOGGER.warning("Could not close MQTT session cleanly", exc_info=True)

    async def _wait_for_wake(self, timeout: float | None = None) -> bool:
        self._wake.clear()
        try:
            if timeout is None:
                await self._wake.wait()
            else:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, timeout))
        except asyncio.TimeoutError:
            return False
        return True

    async def _bridge_loop(self) -> None:
        next_due = self.dependencies.monotonic()
        while not self._closing:
            if not self._can_bridge():
                self.runtime.next_retry_seconds = None
                await self._wait_for_wake()
                next_due = self.dependencies.monotonic()
                continue

            now = self.dependencies.monotonic()
            if next_due > now:
                self.runtime.next_retry_seconds = max(0, int(next_due - now))
                if await self._wait_for_wake(next_due - now):
                    next_due = self.dependencies.monotonic()
                    continue
            if not self._can_bridge():
                continue

            started = self.dependencies.monotonic()
            success = await self._read_cycle()
            interval: float
            if success:
                interval = self.settings.poll_seconds
            else:
                base = retry_delay(
                    self.settings.poll_seconds,
                    self.runtime.consecutive_failures,
                )
                interval = base + self.dependencies.jitter(0.0, min(5.0, base * 0.1))
            next_due = started + interval
            self.runtime.next_retry_seconds = max(
                0,
                int(next_due - self.dependencies.monotonic()),
            )


def load_mqtt(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.mqtt_host:
        return None
    mqtt: dict[str, Any] = {"host": args.mqtt_host, "port": args.mqtt_port}
    if args.mqtt_credentials_file and args.mqtt_credentials_file.exists():
        try:
            credentials = read_json(args.mqtt_credentials_file)
            mqtt["username"] = credentials.get("username")
            mqtt["password"] = credentials.get("password")
        except (OSError, ValueError) as exc:
            LOGGER.warning("Could not read MQTT credentials: %r", exc)
    return mqtt


async def serve(args: argparse.Namespace) -> int:
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    controller = HubController(data_dir=args.data_dir, mqtt=load_mqtt(args))
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_stop(signum: int) -> None:
        LOGGER.info("Received %s; beginning graceful shutdown", signal.Signals(signum).name)
        stop_event.set()

    registered: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop, sig)
        except (NotImplementedError, RuntimeError):
            continue
        registered.append(sig)

    httpd = create_panel_server(
        controller=controller,
        loop=loop,
        port=args.port,
        index_file=args.static_dir / "index.html",
        icon_file=DEFAULT_ICON_FILE,
    )
    server_thread = threading.Thread(
        target=httpd.serve_forever,
        name="weber-panel-http",
        daemon=True,
    )
    server_thread.start()
    LOGGER.info("Weber Connect panel listening on port %s", args.port)

    await controller.start()
    stop_waiter = asyncio.create_task(stop_event.wait(), name="weber-stop-signal")
    fatal_waiter = asyncio.create_task(
        controller.wait_for_fatal_error(),
        name="weber-fatal-runtime",
    )
    try:
        done, _ = await asyncio.wait(
            {stop_waiter, fatal_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if fatal_waiter in done:
            error = fatal_waiter.result()
            LOGGER.critical("Stopping after fatal runtime error: %r", error)
            return 1
        return 0
    finally:
        for waiter in (stop_waiter, fatal_waiter):
            waiter.cancel()
        await asyncio.gather(stop_waiter, fatal_waiter, return_exceptions=True)
        await controller.stop()
        await asyncio.to_thread(httpd.shutdown)
        httpd.server_close()
        server_thread.join(timeout=5)
        for sig in registered:
            loop.remove_signal_handler(sig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Weber Connect ingress panel and bridge.")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--data-dir", type=Path, default=Path("/data/weber-connect-bridge"))
    parser.add_argument("--static-dir", type=Path, default=Path(__file__).parent / "static")
    parser.add_argument("--mqtt-host", default=None)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-credentials-file", type=Path, default=None)
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(serve(args))


if __name__ == "__main__":
    raise SystemExit(main())

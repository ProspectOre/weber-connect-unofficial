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
from weber_cloud import (
    CloudConfig,
    CloudPollResult,
    WeberCloudAuthError,
    WeberCloudClient,
    WeberCloudError,
    resolve_associated_appliance_id,
)
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
DEFAULT_ICON_FILE = Path(__file__).resolve().parent / "static" / "icon.png"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WakeQueue(asyncio.Queue[None]):
    """Coalesce bridge wakeups without losing one before a scheduled wait."""

    def __init__(self) -> None:
        super().__init__(maxsize=1)

    def set(self) -> None:
        try:
            self.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def wait(self) -> None:
        await self.get()


@dataclass(frozen=True, slots=True)
class ControllerDependencies:
    scan: Callable[..., Awaitable[dict[str, Any]]] = ble_scan
    pair: Callable[..., Awaitable[dict[str, Any]]] = pair_once
    read_status: Callable[..., Awaitable[dict[str, Any]]] = read_status_once
    release: Callable[[str], bool] = release_ble_connection
    key_loader: Callable[..., dict[str, Any]] = load_or_create_pairing_keys
    summary_builder: Callable[..., dict[str, Any]] = build_pairing_summary
    mqtt_factory: Callable[..., MqttSession] = MqttSession
    cloud_factory: Callable[..., WeberCloudClient] = WeberCloudClient
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
        self.cloud_file = data_dir / "cloud_credentials.json"
        self.pending_cloud_key_file = data_dir / "pairing_keys.cloud_pending.json"

        self.mqtt_config = MqttConfig.from_mapping(mqtt) if mqtt and mqtt.get("host") else None
        self.settings = BridgeSettings()
        self.summary: dict[str, Any] | None = None
        self.cloud_config: CloudConfig | None = None
        self.runtime = RuntimeState()
        self.dependencies = dependencies or ControllerDependencies()

        self._ble_lock = asyncio.Lock()
        self._cycle_lock = asyncio.Lock()
        # A bounded queue preserves a wake request that arrives just before
        # the bridge begins waiting. Event.clear() made that race lose user
        # actions and cloud-test wakeups until the next retry deadline.
        self._wake = WakeQueue()
        self._supervisor = TaskSupervisor()
        self._mqtt_session: MqttSession | None = None
        self._cloud_client: WeberCloudClient | None = None
        self._auto_resume_task: asyncio.Task[Any] | None = None
        self._started = False
        self._closing = False
        self._fatal_error: BaseException | None = None
        self._fatal_event = asyncio.Event()
        self._event_loop: asyncio.AbstractEventLoop | None = None

        self._load()

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        if self._closing:
            raise RuntimeError("controller is closing")
        self._started = True
        self._event_loop = asyncio.get_running_loop()
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
        await self._close_cloud_client()
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
        if self.cloud_file.exists():
            try:
                self.cloud_config = CloudConfig.from_mapping(read_json(self.cloud_file))
                if (
                    self.cloud_config.identity_source == "bridge"
                    and self.cloud_config.temperature_unit == "fahrenheit"
                ):
                    # Bridge-generated identities created before 1.2.0 used
                    # the wrong default. Walker snapshots encode probe
                    # temperatures as tenths of a degree Celsius.
                    self.cloud_config = self.cloud_config.with_temperature_unit(
                        "deci_celsius"
                    )
                    self._save_cloud()
                self.runtime.cloud_state = (
                    "ready" if self.cloud_config.enabled else "disabled"
                )
            except (OSError, ValueError) as exc:
                self.runtime.cloud_state = "error"
                self.runtime.cloud_error = f"Could not read cloud settings: {exc}"
                LOGGER.warning("Could not read cloud settings: %r", exc)
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

    def _save_cloud(self) -> None:
        if self.cloud_config is None:
            self.cloud_file.unlink(missing_ok=True)
            return
        write_json_atomic(self.cloud_file, self.cloud_config.as_dict())

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

    def _can_cloud(self) -> bool:
        return (
            not self._closing
            and self.paired
            and self.cloud_config is not None
            and self.cloud_config.enabled
            and not self.runtime.scanning
            and not self.runtime.pairing
            and bool(self._appliance_id())
        )

    def _can_read(self) -> bool:
        return self._can_bridge() or self._can_cloud()

    def _appliance_id(self) -> str | None:
        hub = (self.summary or {}).get("hub") or {}
        value = hub.get("appliance_id")
        return value if isinstance(value, str) and value else None

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

    def heartbeat(self) -> dict[str, Any]:
        """Lock-free liveness snapshot for the Supervisor watchdog.

        Reads only plain runtime attributes so it can be served straight from
        the HTTP thread without scheduling work on the event loop; if the loop
        is wedged the watchdog still gets an answer and can restart the add-on.
        """
        return {
            "ok": True,
            "state": self.state(),
            "loop_beat": self.runtime.loop_beat,
            "checked_at": utc_now(),
        }

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
            "active_cook": self.runtime.last_good_state.get("active_cook", {}),
            "probe_count": self.runtime.last_good_state.get("probe_count", 0),
            "max_probes": MAX_PROBES,
            "readings_stale": bool(self.runtime.last_good_state) and not self.runtime.last_read_ok,
            "last_read_at": self.runtime.last_read_at,
            "source": self.runtime.last_source,
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
            "cloud": {
                **(
                    self.cloud_config.public_dict()
                    if self.cloud_config is not None
                    else {"configured": False, "enabled": False}
                ),
                "state": self.runtime.cloud_state,
                "last_poll_at": self.runtime.cloud_last_poll_at,
                "error": self.runtime.cloud_error,
                "live_session_error": getattr(
                    self._cloud_client, "socket_error", None
                ),
                "session_id": self.runtime.cloud_session_id,
                "last_snapshot_id": self.runtime.cloud_after_id,
                "new_snapshots": self.runtime.cloud_snapshot_count,
                "appliance_id_available": self._appliance_id() is not None,
                "pairing_verification_code_available": bool(
                    ((self.summary or {}).get("pairing_response") or {}).get(
                        "verification_code"
                    )
                ),
            },
            "controls": {
                "enabled": self.settings.remote_controls_enabled,
                "available": bool(
                    self.runtime.last_good_state.get("cook_control_available")
                ),
                "last_command_at": self.runtime.control_last_command_at,
                "error": self.runtime.control_error,
            },
            "settings": {
                "poll_seconds": self.settings.poll_seconds,
                "handoff_minutes": self.settings.handoff_minutes,
                "probe_names": {
                    str(number): name
                    for number, name in self.settings.probe_names.items()
                },
                "remote_controls_enabled": self.settings.remote_controls_enabled,
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

    async def pair(
        self,
        address: object,
        *,
        phone_coexistence: object = False,
    ) -> dict[str, Any]:
        if self._closing:
            return {"ok": False, "error": "The bridge is shutting down."}
        if address is not None and not isinstance(address, str):
            return {"ok": False, "error": "address must be a string or null."}
        if not isinstance(phone_coexistence, bool):
            return {"ok": False, "error": "phone_coexistence must be a boolean."}
        if self.runtime.scanning or self.runtime.pairing:
            return {"ok": False, "error": "Another hub operation is already running."}

        prepared_keys = None
        pending_key_file = None
        pending_cloud_config = None
        pending_cloud_client = None
        if phone_coexistence:
            try:
                (
                    prepared_keys,
                    pending_key_file,
                    pending_cloud_config,
                    pending_cloud_client,
                ) = await self._prepare_cloud_companion()
            except Exception as exc:
                await asyncio.to_thread(
                    self.pending_cloud_key_file.unlink,
                    missing_ok=True,
                )
                self.runtime.cloud_state = "unconfigured"
                self.runtime.cloud_error = None
                return {"ok": False, "error": f"Could not prepare phone coexistence: {exc}"}
            self.runtime.cloud_state = "pairing"
            self.runtime.cloud_error = None
        self.runtime.pairing = True
        self.runtime.setup_error = None
        self._wake.set()
        self._supervisor.spawn(
            "weber-pair",
            self._pair_task(
                address.strip() if address else None,
                prepared_keys=prepared_keys,
                pending_key_file=pending_key_file,
                pending_cloud_config=pending_cloud_config,
                pending_cloud_client=pending_cloud_client,
            ),
            on_error=self._operation_error,
        )
        return {"ok": True, "phone_coexistence": phone_coexistence}

    def _log_pair_events(self, result: dict[str, Any]) -> None:
        events = result.get("events") or []
        if not events:
            LOGGER.debug("Hub sent no notifications during pairing")
            return
        for event in events:
            decoded = event.get("decoded") or {}
            envelope = decoded.get("envelope") or {}
            candidate = envelope.get("body_plain_candidate") or {}
            LOGGER.debug(
                "Pairing event source=%s type=%s length=%s",
                event.get("source"),
                candidate.get("type_name") or "UNDECODED",
                event.get("length"),
            )

    async def _pair_task(
        self,
        address: str | None,
        *,
        prepared_keys: dict[str, Any] | None = None,
        pending_key_file: Path | None = None,
        pending_cloud_config: CloudConfig | None = None,
        pending_cloud_client: WeberCloudClient | None = None,
    ) -> None:
        pairing_confirmed = False
        phone_coexistence_ready = False
        try:
            if pending_cloud_client is not None:
                # The Weber companion is a registered cloud device before its
                # id is presented to the hub during the BLE key exchange. The
                # hub can therefore publish the new association while the
                # pairing session is still active.
                await asyncio.to_thread(pending_cloud_client.authenticate)
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
                keys = prepared_keys or self.dependencies.key_loader(
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
                pairing_confirmed = True
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
                if pending_key_file is not None:
                    await asyncio.to_thread(pending_key_file.replace, self.key_file)
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
                if pending_cloud_config is not None and pending_cloud_client is not None:
                    self.cloud_config = pending_cloud_config
                    self._cloud_client = pending_cloud_client
                    self._save_cloud()
                    try:
                        await self._wait_for_cloud_association(pending_cloud_client)
                        # Phone + Home Assistant is the default experience:
                        # leave Bluetooth available to the Weber app while the
                        # bridge follows the cook through its cloud companion.
                        self.settings = self.settings.updated({"handoff_minutes": 0})
                        self._save_settings()
                        phone_coexistence_ready = True
                    except (WeberCloudError, RuntimeError, ValueError) as exc:
                        self.runtime.cloud_state = "error"
                        self.runtime.cloud_error = str(exc)
                        LOGGER.warning(
                            "BLE pairing succeeded, but cloud association could not be verified: %r",
                            exc,
                        )
            if phone_coexistence_ready:
                self.runtime.handoff_active = True
                self.runtime.handoff_until = None
                self.runtime.handoff_token += 1
                self._save_handoff()
                if address:
                    try:
                        await asyncio.to_thread(self.dependencies.release, address)
                    except Exception:
                        LOGGER.warning(
                            "Could not release Bluetooth after cloud setup",
                            exc_info=True,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error("Pairing failed: %r", exc)
            self.runtime.setup_error = str(exc)
        finally:
            if pending_key_file is not None:
                await asyncio.to_thread(pending_key_file.unlink, missing_ok=True)
            if pending_cloud_config is not None and not pairing_confirmed:
                self.runtime.cloud_state = "unconfigured"
                self.runtime.cloud_error = None
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
        self.runtime.last_read_ok = False
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
            self.cloud_config = None
            await self._close_cloud_client()
            self.runtime.cloud_state = "unconfigured"
            self.runtime.cloud_error = None
            for path in (
                self.summary_file,
                self.status_file,
                self.handoff_file,
                self.cloud_file,
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    LOGGER.warning("Could not remove %s: %r", path, exc)
        self._wake.set()
        return {"ok": True}

    async def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("remote_controls_enabled") is True and (
            self.cloud_config is None or not self.cloud_config.enabled
        ):
            return {
                "ok": False,
                "error": "Set up Weber app access before enabling remote cook controls.",
            }
        previous_probe_names = self.settings.probe_names
        previous_controls_enabled = self.settings.remote_controls_enabled
        try:
            updated = self.settings.updated(payload)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.settings = updated
        self._save_settings()
        if (
            (
                updated.probe_names != previous_probe_names
                or updated.remote_controls_enabled != previous_controls_enabled
            )
            and self.runtime.last_good_state
            and self.summary is not None
            and self.address is not None
        ):
            current = self.runtime.last_good_state
            refreshed = build_state(
                self.summary,
                current.get("status") or {},
                self.address,
                connected=bool(current.get("connected")),
                max_probes=MAX_PROBES,
                source=self.runtime.last_source or str(current.get("source") or "ble"),
                probe_names=updated.probe_names,
                remote_controls_enabled=updated.remote_controls_enabled,
            )
            self.runtime.last_good_state = refreshed
            try:
                write_json_atomic(self.status_file, refreshed)
            except OSError as exc:
                LOGGER.warning("Could not persist renamed probe snapshot: %r", exc)
            await self._publish(refreshed)
        self._wake.set()
        return {"ok": True, "settings": self.settings.as_dict()}

    def _new_cloud_client(self) -> WeberCloudClient:
        if self.cloud_config is None:
            raise RuntimeError("Cloud fallback is not configured.")
        if self._cloud_client is not None:
            close = getattr(self._cloud_client, "close", None)
            if callable(close):
                close()
        self._cloud_client = self.dependencies.cloud_factory(self.cloud_config)
        return self._cloud_client

    async def _close_cloud_client(self) -> None:
        client, self._cloud_client = self._cloud_client, None
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)

    async def _verify_cloud_appliance_access(
        self,
        client: WeberCloudClient,
        appliances: list[dict[str, Any]] | None = None,
    ) -> str:
        expected_appliance_id = self._appliance_id()
        if appliances is None:
            appliances = await asyncio.to_thread(client.associated_appliances)
        appliance_id = resolve_associated_appliance_id(
            appliances,
            expected_appliance_id,
        )
        if appliance_id is None:
            raise WeberCloudAuthError(
                "Cloud identity is not authorized for this hub. Pair the bridge "
                "with the hub and confirm the request on its display."
            )
        try:
            # A successful sessions request proves more than companion login:
            # it proves this identity may read this specific hub. An empty
            # session list is still a valid result when no cook is active.
            await asyncio.to_thread(client.latest_session_id, appliance_id)
        except WeberCloudAuthError as exc:
            if "HTTP 403" in str(exc):
                raise WeberCloudAuthError(
                    "Cloud identity is not authorized for this hub. Enter the "
                    "Wi-Fi provisioning verification code or use already-associated "
                    "companion credentials."
                ) from exc
            raise
        if self.cloud_config is not None and self.cloud_config.appliance_id != appliance_id:
            self.cloud_config = self.cloud_config.with_appliance_id(appliance_id)
            self._save_cloud()
            if self._cloud_client is not None:
                self._cloud_client.config = self.cloud_config
        return appliance_id

    async def _test_cloud(
        self, *, verify_appliance: bool = True
    ) -> list[dict[str, Any]]:
        client = self._cloud_client or self._new_cloud_client()
        await asyncio.to_thread(client.authenticate)
        appliances = await asyncio.to_thread(client.associated_appliances)
        if verify_appliance:
            await self._verify_cloud_appliance_access(client, appliances)
            self.runtime.cloud_state = "ready"
            self.runtime.cloud_error = None
        return appliances

    async def _wait_for_cloud_association(
        self,
        client: WeberCloudClient,
        *,
        timeout: float = 300.0,
    ) -> str:
        deadline = asyncio.get_running_loop().time() + timeout
        last_error: Exception | None = None
        while True:
            try:
                appliances = await asyncio.to_thread(client.associated_appliances)
                appliance_id = await self._verify_cloud_appliance_access(
                    client,
                    appliances,
                )
                self.runtime.cloud_state = "ready"
                self.runtime.cloud_error = None
                return appliance_id
            except (WeberCloudError, RuntimeError, ValueError) as exc:
                last_error = exc
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                assert last_error is not None
                raise last_error
            await asyncio.sleep(min(2.0, remaining))

    async def _prepare_cloud_companion(
        self,
    ) -> tuple[dict[str, Any], Path, CloudConfig, WeberCloudClient]:
        await asyncio.to_thread(self.pending_cloud_key_file.unlink, missing_ok=True)
        keys = self.dependencies.key_loader(
            path=self.pending_cloud_key_file,
            display_name="Home Assistant",
            companion_id=None,
            companion_public_key=None,
            reset_key=True,
        )
        cloud_config = CloudConfig.generate(str(keys["companion_id"]))
        cloud_client = self.dependencies.cloud_factory(cloud_config)
        return keys, self.pending_cloud_key_file, cloud_config, cloud_client

    async def _start_cloud_pairing(self) -> None:
        if not self.paired or self.summary is None or not self.address:
            raise ValueError("Pair a hub locally before enabling cloud access.")
        if self.cloud_config is not None:
            raise ValueError("Remove the existing cloud credentials before pairing a new identity.")
        if self.runtime.scanning or self.runtime.pairing:
            raise ValueError("Another hub operation is already running.")

        keys, pending_key_file, cloud_config, cloud_client = (
            await self._prepare_cloud_companion()
        )

        self.runtime.cloud_state = "pairing"
        self.runtime.cloud_error = None
        self.runtime.pairing = True
        self.runtime.setup_error = None
        self._wake.set()
        self._supervisor.spawn(
            "weber-cloud-pair",
            self._pair_task(
                self.address,
                prepared_keys=keys,
                pending_key_file=pending_key_file,
                pending_cloud_config=cloud_config,
                pending_cloud_client=cloud_client,
            ),
            on_error=self._operation_error,
        )

    async def update_cloud(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply an explicit experimental-cloud action from the ingress panel."""

        action = payload.get("action")
        if not isinstance(action, str):
            return {"ok": False, "error": "Cloud action is required."}
        association_attempted = False
        pairing_started = False
        try:
            if action == "pair":
                await self._start_cloud_pairing()
                appliances = []
                pairing_started = True
            elif action == "create":
                if not self.paired or self.summary is None:
                    raise ValueError("Pair a hub before creating a bridge cloud identity.")
                self.cloud_config = CloudConfig.generate(self.summary["companion_id"])
                self._save_cloud()
                self._new_cloud_client()
                appliances = await self._test_cloud(verify_appliance=False)
                verification_code = ((self.summary.get("pairing_response") or {}).get(
                    "verification_code"
                ))
                if not appliances and verification_code:
                    association_attempted = True
                    assert self._cloud_client is not None
                    await asyncio.to_thread(
                        self._cloud_client.associate,
                        str(verification_code),
                    )
                    appliances = await asyncio.to_thread(
                        self._cloud_client.associated_appliances
                    )
                assert self._cloud_client is not None
                await self._verify_cloud_appliance_access(self._cloud_client)
                self.runtime.cloud_state = "ready"
                self.runtime.cloud_error = None
            elif action == "save":
                self.cloud_config = CloudConfig.from_mapping(
                    {
                        "device_id": payload.get("device_id"),
                        "device_password": payload.get("device_password"),
                        "temperature_unit": payload.get("temperature_unit"),
                        "identity_source": "manual",
                        "enabled": True,
                    }
                )
                self._save_cloud()
                self._new_cloud_client()
                appliances = await self._test_cloud()
            elif action == "test":
                appliances = await self._test_cloud()
            elif action == "associate":
                client = self._cloud_client or self._new_cloud_client()
                code = payload.get("verification_code")
                if not code and self.summary:
                    code = ((self.summary.get("pairing_response") or {}).get(
                        "verification_code"
                    ))
                if not isinstance(code, (str, int)) or not str(code).strip():
                    raise ValueError("Enter the verification code produced during setup.")
                await asyncio.to_thread(client.associate, str(code))
                appliances = await asyncio.to_thread(client.associated_appliances)
                await self._verify_cloud_appliance_access(client)
                self.runtime.cloud_state = "ready"
                self.runtime.cloud_error = None
            elif action in {"enable", "disable"}:
                if self.cloud_config is None:
                    raise ValueError("Cloud fallback is not configured.")
                self.cloud_config = self.cloud_config.with_enabled(action == "enable")
                self._save_cloud()
                if action == "disable":
                    await self._close_cloud_client()
                    if self.settings.remote_controls_enabled:
                        await self.update_settings({"remote_controls_enabled": False})
                self.runtime.cloud_state = "ready" if action == "enable" else "disabled"
                self.runtime.cloud_error = None
                appliances = []
            elif action == "remove":
                self.cloud_config = None
                await self._close_cloud_client()
                self._save_cloud()
                if self.settings.remote_controls_enabled:
                    await self.update_settings({"remote_controls_enabled": False})
                self.runtime.cloud_state = "unconfigured"
                self.runtime.cloud_error = None
                self.runtime.cloud_session_id = None
                self.runtime.cloud_after_id = 0
                self.runtime.cloud_snapshot_count = 0
                appliances = []
            else:
                return {"ok": False, "error": "Unknown cloud action."}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.runtime.cloud_state = "error"
            self.runtime.cloud_error = str(exc)
            LOGGER.warning("Cloud action %s failed: %r", action, exc)
            return {"ok": False, "error": str(exc)}

        if (
            action in {"disable", "remove"}
            and self.runtime.last_source == "cloud"
            and self.runtime.last_read_ok
        ):
            await self._record_read_failure(
                "Cloud fallback was disabled."
                if action == "disable"
                else "Cloud fallback credentials were removed."
            )
        self._wake.set()
        return {
            "ok": True,
            "cloud": (
                self.cloud_config.public_dict()
                if self.cloud_config is not None
                else {"configured": False, "enabled": False}
            ),
            "associated_appliances": len(appliances),
            "association_attempted": association_attempted,
            "pairing_started": pairing_started,
        }

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
                source=self.runtime.last_source or "ble",
                probe_names=self.settings.probe_names,
                remote_controls_enabled=self.settings.remote_controls_enabled,
            )
            try:
                write_json_atomic(self.status_file, disconnected)
            except OSError as exc:
                LOGGER.warning("Could not persist disconnected status: %r", exc)
            await self._publish(disconnected)
        return False

    async def _read_cycle(self) -> bool:
        async with self._cycle_lock:
            return await self._read_cycle_once()

    async def _read_cycle_once(self) -> bool:
        failure: str | None = None
        if self._can_bridge() and self.summary and self.address:
            async with self._ble_lock:
                if self._can_bridge():
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
                        LOGGER.warning("BLE read failed: %r", exc)
                        failure = f"Could not reach the hub over Bluetooth: {exc}"
                    else:
                        latest = result.get("latest_status")
                        if latest:
                            return await self._accept_status(
                                latest,
                                source="ble",
                                connected=bool(result.get("connected")),
                            )
                        failure = "Connected over Bluetooth, but the hub sent no probe status."

        if self._can_cloud():
            try:
                return await self._read_cloud_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.runtime.cloud_state == "idle":
                    self.runtime.cloud_error = None
                else:
                    self.runtime.cloud_state = "error"
                    self.runtime.cloud_error = str(exc)
                LOGGER.warning("Cloud fallback failed: %r", exc)
                cloud_failure = f"Cloud fallback failed: {exc}"
                failure = f"{failure} {cloud_failure}" if failure else cloud_failure

        if failure:
            return await self._record_read_failure(failure)
        return False

    async def _read_cloud_once(self) -> bool:
        appliance_id = (
            self.cloud_config.appliance_id if self.cloud_config is not None else None
        ) or self._appliance_id()
        if appliance_id is None:
            raise RuntimeError("BLE pairing did not provide a cloud appliance ID.")
        client = self._cloud_client or self._new_cloud_client()
        result: CloudPollResult | None = await asyncio.to_thread(client.poll, appliance_id)
        self.runtime.cloud_last_poll_at = utc_now()
        if result is None:
            self.runtime.cloud_state = "idle"
            self.runtime.cloud_error = None
            self.runtime.cloud_session_id = None
            self.runtime.cloud_after_id = 0
            self.runtime.cloud_snapshot_count = 0
            # No active cook is a healthy cloud result. Publishing an empty
            # snapshot clears an ended recipe and keeps the normal poll cadence
            # instead of escalating into exponential transport backoff.
            return await self._accept_status(
                {
                    "kind": "cloud_idle",
                    "probe_count": 0,
                    "probes": [],
                    "cavities": [],
                    "timers": [],
                    "active_cook": None,
                },
                source="cloud",
            )
        self.runtime.cloud_state = "online"
        self.runtime.cloud_error = None
        self.runtime.cloud_session_id = result.session_id
        self.runtime.cloud_after_id = result.after_id
        self.runtime.cloud_snapshot_count = result.snapshot_count
        return await self._accept_status(result.status, source="cloud")

    async def _accept_status(
        self,
        latest: dict[str, Any],
        *,
        source: str,
        connected: bool = True,
    ) -> bool:
        if self.summary is None or self.address is None:
            return False
        self.runtime.last_read_ok = True
        self.runtime.last_source = source
        self.runtime.last_error = None
        self.runtime.last_read_at = utc_now()
        self.runtime.consecutive_failures = 0
        state = build_state(
            self.summary,
            latest,
            self.address,
            connected=connected,
            max_probes=MAX_PROBES,
            source=source,
            probe_names=self.settings.probe_names,
            remote_controls_enabled=self.settings.remote_controls_enabled,
        )
        self.runtime.last_good_state = state
        try:
            write_json_atomic(self.status_file, state)
        except OSError as exc:
            # A failed status write must never crash the bridge runtime; treat
            # it as a recoverable read failure so the loop keeps polling.
            LOGGER.warning("Could not persist status snapshot: %r", exc)
            return await self._record_read_failure(f"Could not save status: {exc}")
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
                    discovery_cache_file=self.data_dir / "discovery_cache.json",
                    command_handler=self._mqtt_command_received,
                )
            await self._mqtt_session.publish(state, self.settings.poll_seconds)
            self.runtime.mqtt_published_at = utc_now()
            self.runtime.mqtt_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.runtime.mqtt_error = str(exc)
            LOGGER.error("MQTT publish failed: %r", exc)

    def _mqtt_command_received(self, topic: str, payload: str) -> None:
        if self._event_loop is None or self._closing:
            return
        future = asyncio.run_coroutine_threadsafe(
            self.remote_command(topic, payload), self._event_loop
        )

        def command_finished(completed: Any) -> None:
            try:
                completed.result()
            except Exception:
                LOGGER.warning("Remote cook command failed", exc_info=True)

        future.add_done_callback(command_finished)

    async def remote_command(self, topic: str, payload: str) -> dict[str, Any]:
        """Validate and route an explicitly enabled MQTT cook command."""

        try:
            await self._execute_remote_command(topic, payload)
        except Exception as exc:
            self.runtime.control_error = str(exc)
            raise
        self.runtime.control_last_command_at = utc_now()
        self.runtime.control_error = None
        self._wake.set()
        return {"ok": True}

    async def panel_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Route an opt-in cook command from the trusted ingress panel."""

        kind = payload.get("type")
        action = payload.get("action")
        if kind == "cook" and action in {"confirm", "stop"}:
            return await self.remote_command(
                f"panel/command/cook/{action}",
                str(action),
            )
        if kind == "timer" and action in {"start", "reset"}:
            number = parse_whole_number(payload.get("number"), "timer number")
            value = payload.get("duration_s") if action == "start" else "reset"
            return await self.remote_command(
                f"panel/command/timer/{number}/{action}",
                str(value),
            )
        raise ValueError("Unsupported panel cook command.")

    async def _execute_remote_command(self, topic: str, payload: str) -> None:
        if not self.settings.remote_controls_enabled:
            raise ValueError("Remote cook controls are disabled.")
        if self.cloud_config is None or not self.cloud_config.enabled:
            raise ValueError("Weber Cloud access is required for remote controls.")
        appliance_id = self.cloud_config.appliance_id or self._appliance_id()
        if appliance_id is None:
            raise ValueError("The paired hub has no cloud appliance ID.")
        marker = "/command/"
        if marker not in topic:
            raise ValueError("Invalid command topic.")
        route = topic.split(marker, 1)[1].strip("/").split("/")
        client = self._cloud_client or self._new_cloud_client()
        if len(route) == 2 and route[0] == "cook" and route[1] in {"confirm", "stop"}:
            action = route[1]
            if payload.strip().lower() != action:
                raise ValueError("Cook command payload does not match its topic.")
            active_cook = (self.runtime.last_good_state.get("status") or {}).get(
                "active_cook"
            )
            if not isinstance(active_cook, dict) or not active_cook.get("active"):
                raise ValueError("No active cook is available for this command.")
            await asyncio.to_thread(
                client.session_command,
                appliance_id,
                active_cook,
                action,
            )
        elif (
            len(route) == 3
            and route[0] == "timer"
            and route[1].isdigit()
            and route[2] in {"start", "reset"}
        ):
            timer_number = int(route[1])
            if timer_number < 1 or timer_number > 4:
                raise ValueError("Timer number must be between 1 and 4.")
            action = route[2]
            duration_s = 0
            if action == "start":
                duration_s = parse_whole_number(payload.strip(), "timer duration")
            elif payload.strip().lower() != "reset":
                raise ValueError("Timer reset payload is invalid.")
            await asyncio.to_thread(
                client.timer_command,
                appliance_id,
                timer_number - 1,
                action,
                duration_s,
            )
        else:
            raise ValueError("Unsupported remote cook command.")

    async def _close_mqtt(self) -> None:
        session, self._mqtt_session = self._mqtt_session, None
        if session is None:
            return
        try:
            await asyncio.wait_for(session.close(), timeout=20.0)
        except Exception:
            LOGGER.warning("Could not close MQTT session cleanly", exc_info=True)

    async def _wait_for_wake(self, timeout: float | None = None) -> bool:
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
            self.runtime.loop_beat = utc_now()
            if not self._can_read():
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
            if not self._can_read():
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

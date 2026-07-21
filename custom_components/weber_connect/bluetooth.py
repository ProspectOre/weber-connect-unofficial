"""Weber GATT protocol over Home Assistant's local and proxy scanners."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from bleak_retry_connector import BleakOutOfConnectionSlotsError, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import NAME
from .models import CompanionIdentity, PairingResult
from .saber_frames import (
    COMMAND_UUID,
    NOTIFICATION_UUID,
    RESPONSE_UUID,
    SESSION_UUID,
    STATUS_UUID,
    build_command_frame,
    build_handshake_body,
    build_pairing_body,
    decode_hex_frame,
)

_LOGGER = logging.getLogger(__name__)
CONNECTION_TIMEOUT = 30.0
STATUS_INTERVAL = 10.0
RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)

StatusCallback = Callable[[dict[str, Any]], None]
ErrorCallback = Callable[[str], None]


class WeberBluetoothError(RuntimeError):
    """A hub was unavailable or returned an invalid protocol response."""


class WeberBluetoothSession:
    """Own one long-lived local connection to a Weber hub.

    ESPHome proxies are designed to keep an allocated remote GATT connection
    open. Reconnecting for every temperature sample adds several scan windows,
    races slot cleanup, and can leave the next attempt waiting on a stale link.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        companion_id: str,
        message_version: int,
    ) -> None:
        self.hass = hass
        self.address = address
        self.companion_id = companion_id
        self.message_version = message_version
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._received = asyncio.Event()
        self._wake = asyncio.Event()
        self._latest: dict[str, Any] | None = None
        self._services_ready = False
        self._status_callback: StatusCallback | None = None
        self._sequence = 1
        self._session_started = False
        self._closed = False

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence = 1 if value >= 0xFFFFFFFF else value + 1
        return value

    def _handle_status(self, _sender: Any, data: bytearray) -> None:
        _type_value, parsed, _code = _payload(bytes(data))
        if isinstance(parsed, dict) and parsed.get("kind") == "cook_session_status":
            self._latest = parsed
            self._received.set()
            if self._status_callback is not None:
                self._status_callback(parsed)

    def _handle_disconnect(self, _client: BleakClient) -> None:
        self._wake.set()

    async def _async_disconnect_locked(self) -> None:
        client = self._client
        self._client = None
        self._session_started = False
        if client is not None:
            await _safe_disconnect(client)

    async def _async_connect_locked(self) -> BleakClient:
        """Connect once, refreshing GATT services when the proxy needs it."""

        strategies = (True, False) if self._services_ready else (False,)
        for use_services_cache in strategies:
            client = await _connect(
                self.hass,
                self.address,
                use_services_cache=use_services_cache,
                disconnected_callback=self._handle_disconnect,
            )
            subscriptions = 0
            cache_error: BleakCharacteristicNotFoundError | None = None
            try:
                for uuid in (STATUS_UUID, NOTIFICATION_UUID, RESPONSE_UUID):
                    try:
                        await client.start_notify(uuid, self._handle_status)
                        subscriptions += 1
                    except BleakCharacteristicNotFoundError as exc:
                        cache_error = exc
                    except Exception:
                        _LOGGER.debug(
                            "Hub characteristic %s does not notify",
                            uuid,
                            exc_info=True,
                        )
                if subscriptions == 0 and cache_error is not None:
                    raise cache_error
                if subscriptions == 0:
                    await _safe_disconnect(client)
                    raise WeberBluetoothError(
                        "The hub did not expose a usable status notification. "
                        "Home Assistant will retry automatically."
                    )
            except BleakCharacteristicNotFoundError as exc:
                await _safe_disconnect(client)
                self._services_ready = False
                if not use_services_cache:
                    raise WeberBluetoothError(
                        "The hub's Bluetooth services could not be discovered. "
                        "Home Assistant will retry automatically."
                    ) from exc
                continue
            self._client = client
            self._services_ready = True
            self._sequence = 1
            self._session_started = False
            return client
        raise WeberBluetoothError(
            "The hub's Bluetooth services could not be discovered. Home Assistant will retry automatically."
        )

    async def async_read_status(self, *, timeout: float = 12.0) -> dict[str, Any]:
        """Request and return a fresh status while retaining the GATT link."""

        async with self._lock:
            client = self._client
            if client is None or not client.is_connected:
                await self._async_disconnect_locked()
                client = await self._async_connect_locked()

            self._received.clear()
            if self._session_started:
                characteristic = COMMAND_UUID
                frame = build_command_frame(
                    self._next_sequence(),
                    self.message_version,
                    0x05,
                    b"",
                )
            else:
                characteristic = SESSION_UUID
                frame = build_command_frame(
                    self._next_sequence(),
                    self.message_version,
                    0x70,
                    build_handshake_body(self.companion_id, secrets.token_bytes(32)),
                )
            try:
                await client.write_gatt_char(characteristic, frame, response=True)
                self._session_started = True
                await asyncio.wait_for(self._received.wait(), timeout=timeout)
            except TimeoutError as exc:
                if not client.is_connected:
                    await self._async_disconnect_locked()
                raise WeberBluetoothError(
                    "The hub connected but did not return a fresh probe reading. "
                    "Home Assistant will retry automatically."
                ) from exc
            except BleakError as exc:
                await self._async_disconnect_locked()
                self._services_ready = False
                raise WeberBluetoothError(
                    "The Bluetooth session was interrupted. Home Assistant will retry automatically."
                ) from exc

            if self._latest is None:
                raise WeberBluetoothError("The hub returned an empty status response.")
            return self._latest

    async def async_run(
        self,
        status_callback: StatusCallback,
        error_callback: ErrorCallback,
    ) -> None:
        """Maintain the local session and publish fresh notifications."""

        self._status_callback = status_callback
        delay_index = 0
        try:
            while not self._closed:
                # Clear before doing I/O so an advertisement received during a
                # request remains set and advances the next attempt promptly.
                self._wake.clear()
                started = asyncio.get_running_loop().time()
                try:
                    await self.async_read_status()
                except WeberBluetoothError as exc:
                    error_callback(str(exc))
                    delay = RECONNECT_DELAYS[min(delay_index, len(RECONNECT_DELAYS) - 1)]
                    delay_index += 1
                else:
                    delay_index = 0
                    delay = max(
                        1.0,
                        STATUS_INTERVAL - (asyncio.get_running_loop().time() - started),
                    )
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            self._status_callback = None
            await self.async_close()

    def async_wake(self) -> None:
        """Retry promptly when Home Assistant sees a fresh advertisement."""

        self._wake.set()

    async def async_close(self) -> None:
        """Release the proxy slot when the config entry unloads."""

        self._closed = True
        self._wake.set()
        async with self._lock:
            await self._async_disconnect_locked()


def generate_identity() -> CompanionIdentity:
    """Generate the opaque identity shape used by the official companion."""

    return CompanionIdentity(
        companion_id=secrets.token_hex(16),
        private_key=secrets.token_hex(64),
        public_key=secrets.token_hex(64),
    )


def _decoded(data: bytes) -> dict[str, Any]:
    return decode_hex_frame(data.hex(":"))


def _payload(data: bytes) -> tuple[int | None, dict[str, Any] | None, int | None]:
    decoded = _decoded(data)
    envelope = decoded.get("envelope") or {}
    candidate = envelope.get("body_plain_candidate") or {}
    return (
        candidate.get("type_value"),
        candidate.get("parsed_payload"),
        envelope.get("verification_code"),
    )


async def _connect(
    hass: HomeAssistant,
    address: str,
    *,
    max_attempts: int = 1,
    use_services_cache: bool = True,
    disconnected_callback: Callable[[BleakClient], None] | None = None,
) -> BleakClient:
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        reason = bluetooth.async_address_reachability_diagnostics(
            hass,
            address,
            bluetooth.BluetoothReachabilityIntent.CONNECTION,
        )
        raise WeberBluetoothError(
            "The hub is not reachable from an active Home Assistant Bluetooth adapter or proxy. "
            f"{reason}"
        )
    try:
        return await establish_connection(
            BleakClient,
            device,
            NAME,
            disconnected_callback=disconnected_callback,
            # Live reads already retry on the coordinator cadence. Keeping one
            # connector attempt prevents a sleeping hub or a starting proxy
            # from holding up Home Assistant startup for several minutes.
            max_attempts=max_attempts,
            use_services_cache=use_services_cache,
            # ESPHome proxies may need multiple scan windows before the remote
            # GATT link is allocated. Ten seconds was enough for local radios
            # but cancelled healthy proxy attempts before they could finish.
            timeout=CONNECTION_TIMEOUT,
            ble_device_callback=lambda: (
                bluetooth.async_ble_device_from_address(hass, address, connectable=True) or device
            ),
        )
    except BleakOutOfConnectionSlotsError as exc:
        raise WeberBluetoothError(
            "Every Bluetooth proxy connection slot is currently busy. Home Assistant will retry automatically."
        ) from exc
    except (BleakError, TimeoutError) as exc:
        raise WeberBluetoothError(
            "The Bluetooth connection could not be established. Home Assistant will retry automatically."
        ) from exc


async def _safe_disconnect(client: BleakClient) -> None:
    try:
        async with asyncio.timeout(5.0):
            await client.disconnect()
    except Exception:
        _LOGGER.debug("Could not disconnect from the Weber hub cleanly", exc_info=True)


async def async_pair(
    hass: HomeAssistant,
    address: str,
    identity: CompanionIdentity,
    *,
    display_name: str = "Home Assistant",
    initial_version: int = 11,
    confirmation_timeout: float = 60.0,
) -> PairingResult:
    """Pair Home Assistant after the user confirms on the physical hub."""

    replies: asyncio.Queue[bytes] = asyncio.Queue()
    last_polled_response = b""

    def notify(_sender: Any, data: bytearray) -> None:
        replies.put_nowait(bytes(data))

    # A hub that has just restarted can advertise before its complete GATT
    # table is available through a proxy. Reconnect before asking the user for
    # approval; no pairing request has reached the hub at this point.
    client: BleakClient | None = None
    for service_attempt in range(3):
        pairing_client = await _connect(
            hass,
            address,
            max_attempts=3,
            use_services_cache=False,
        )
        try:
            for uuid in (RESPONSE_UUID, STATUS_UUID, NOTIFICATION_UUID):
                try:
                    await pairing_client.start_notify(uuid, notify)
                except BleakCharacteristicNotFoundError:
                    raise
                except Exception:
                    _LOGGER.debug(
                        "Hub characteristic %s does not notify",
                        uuid,
                        exc_info=True,
                    )
            await pairing_client.write_gatt_char(SESSION_UUID, b"\x01", response=True)
        except BleakCharacteristicNotFoundError as exc:
            await _safe_disconnect(pairing_client)
            bluetooth.async_clear_advertisement_history(hass, address)
            if service_attempt == 2:
                raise WeberBluetoothError(
                    "The hub connected, but its Bluetooth services were not ready. "
                    "Wake the hub and try pairing again."
                ) from exc
            _LOGGER.debug(
                "Weber pairing services were incomplete; reconnecting (%s/3)",
                service_attempt + 1,
            )
            await asyncio.sleep(float(service_attempt + 1))
            continue
        client = pairing_client
        break

    if client is None:
        raise WeberBluetoothError(
            "The hub connected, but its Bluetooth services were not ready. "
            "Wake the hub and try pairing again."
        )

    async def poll_response(timeout: float) -> bytes | None:
        nonlocal last_polled_response
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                queued = replies.get_nowait()
            except asyncio.QueueEmpty:
                queued = b""
            if queued:
                return queued
            try:
                value = bytes(await client.read_gatt_char(RESPONSE_UUID))
            except Exception:
                value = b""
            if value and value != last_polled_response:
                last_polled_response = value
                return value
            await asyncio.sleep(0.25)
        return None

    try:
        version = initial_version
        sequence = 1
        for _attempt in range(3):
            greeting = build_command_frame(
                sequence,
                version,
                0x70,
                build_handshake_body(identity.companion_id, secrets.token_bytes(32)),
            )
            sequence += 1
            await client.write_gatt_char(COMMAND_UUID, greeting, response=True)
            reply = await poll_response(10.0)
            if reply is None:
                continue
            type_value, parsed, _code = _payload(reply)
            if type_value in {0xF1, 0xF2}:
                break
            if (
                isinstance(parsed, dict)
                and parsed.get("kind") == "error"
                and parsed.get("error_type") == "UNSUPPORTED_MESSAGE_VERSION"
            ):
                decoded = _decoded(reply)
                candidate = (decoded.get("envelope") or {}).get("body_plain_candidate") or {}
                candidate_version = candidate.get("message_version")
                if isinstance(candidate_version, int):
                    version = candidate_version

        pairing_body = build_pairing_body(
            identity.companion_id,
            identity.public_key,
            display_name,
        )
        pairing = build_command_frame(sequence, version, 0x0A, pairing_body)
        await client.write_gatt_char(COMMAND_UUID, pairing, response=True)

        deadline = asyncio.get_running_loop().time() + confirmation_timeout
        pairing_payload: dict[str, Any] | None = None
        verification_code: int | None = None
        while asyncio.get_running_loop().time() < deadline:
            reply = await poll_response(min(2.0, deadline - asyncio.get_running_loop().time()))
            if reply is None:
                continue
            _type_value, parsed, code = _payload(reply)
            if isinstance(parsed, dict) and parsed.get("kind") == "pairing_response":
                pairing_payload = parsed
                verification_code = code if isinstance(code, int) and code > 0 else None
                break
        if pairing_payload is None:
            raise WeberBluetoothError(
                "The hub did not confirm pairing. Wake it, approve the request on its display, and try again."
            )
        if pairing_payload.get("status") != "CONFIRMED":
            raise WeberBluetoothError(
                f"The hub returned {pairing_payload.get('status', 'an unknown result')} for pairing."
            )
        appliance_id = str(pairing_payload.get("appliance_id") or "").replace(":", "")
        appliance_public_key = str(pairing_payload.get("appliance_public_key") or "")
        if len(appliance_id) != 32:
            raise WeberBluetoothError("The hub returned an invalid appliance identity.")

        post_pair = build_command_frame(
            sequence + 1,
            version,
            0x70,
            build_handshake_body(identity.companion_id, secrets.token_bytes(32)),
        )
        await client.write_gatt_char(COMMAND_UUID, post_pair, response=True)
        await poll_response(5.0)
        return PairingResult(
            message_version=version,
            appliance_id=appliance_id,
            appliance_public_key=appliance_public_key,
            verification_code=verification_code,
        )
    except BleakCharacteristicNotFoundError as exc:
        raise WeberBluetoothError(
            "The hub's Bluetooth services changed during pairing. "
            "Wake the hub and try pairing again."
        ) from exc
    finally:
        # Disconnecting a Bleak client also removes its notification callbacks.
        # Avoid extra GATT stop-notify traffic after a link has already dropped.
        await _safe_disconnect(client)
        bluetooth.async_clear_advertisement_history(hass, address)

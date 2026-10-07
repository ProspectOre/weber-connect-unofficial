"""Weber GATT protocol over Home Assistant's local and proxy scanners."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import NAME
from .models import BluetoothFrameSummary, CompanionIdentity, PairingResult
from .saber_frames import (
    COMMAND_UUID,
    NOTIFICATION_UUID,
    RESPONSE_UUID,
    SESSION_UUID,
    STATUS_UUID,
    build_command_frame,
    build_handshake_body,
    build_pairing_body,
    crc8,
    decode_hex_frame,
)
from .support import SupportEvent

_LOGGER = logging.getLogger(__name__)
CONNECTION_TIMEOUT = 30.0
PAIRING_RESPONSE_TYPES = frozenset({0x85, 0x87, 0xF0, 0xF1, 0xF2})
MAX_PAIRING_NOTIFICATIONS = 8


class WeberBluetoothError(RuntimeError):
    """A hub was unavailable or returned an invalid protocol response."""


class WeberBluetoothTelemetryError(WeberBluetoothError):
    """An unauthenticated status frame appeared on the setup-only channel."""


class WeberBluetoothFrameError(WeberBluetoothError):
    """A rejected frame with privacy-safe structural evidence for support."""

    def __init__(self, message: str, data: bytes, decoded: dict[str, Any]) -> None:
        super().__init__(message)
        envelope = decoded.get("envelope") or {}
        self.frame_summary = BluetoothFrameSummary(
            received_bytes=len(data),
            transport_present="length_ok" in decoded,
            transport_length_ok=decoded.get("length_ok"),
            transport_has_extra=bool(decoded.get("extra_hex")),
            envelope_present=bool(envelope),
            envelope_crc_ok=envelope.get("crc_ok"),
            envelope_tail_ok=envelope.get("tail_byte") == 0x54 if envelope else None,
        )


def generate_identity() -> CompanionIdentity:
    """Generate the opaque identity shape used by the official companion."""

    return CompanionIdentity(
        companion_id=secrets.token_hex(16),
        public_key=secrets.token_hex(64),
    )


def _decoded(data: bytes) -> dict[str, Any]:
    return decode_hex_frame(data.hex(":"))


def _payload(data: bytes) -> tuple[int | None, dict[str, Any] | None]:
    """Decode a structurally valid plaintext appliance payload.

    The hub wraps every local message in both a transport frame and a Saber
    envelope. Never parse a plausible body out of a truncated, corrupted, or
    concatenated frame. Structural checks do not authenticate the peer, so
    callers must also enforce the message types allowed in their trust flow.
    """

    decoded = _decoded(data)
    if decoded.get("length_ok") is not True or decoded.get("extra_hex"):
        raise WeberBluetoothFrameError(
            "The hub returned an invalid transport frame.", data, decoded
        )
    envelope = decoded.get("envelope") or {}
    if (
        envelope.get("crc_ok") is not True
        or envelope.get("tail_byte") != 0x54
        or envelope.get("extra_hex")
    ):
        raise WeberBluetoothFrameError(
            "The hub returned a corrupted protocol envelope.", data, decoded
        )
    candidate = envelope.get("body_plain_candidate")
    if not isinstance(candidate, dict):
        raise WeberBluetoothError("The hub returned an unsupported encrypted response.")
    return (
        candidate.get("type_value"),
        candidate.get("parsed_payload"),
    )


def _pairing_payload(data: bytes) -> tuple[int, dict[str, Any] | None]:
    """Accept only setup messages eligible for later cloud association checks."""

    type_value, parsed = _payload(data)
    if type_value not in PAIRING_RESPONSE_TYPES:
        raise WeberBluetoothTelemetryError(
            "Ignored unauthenticated Bluetooth telemetry during companion pairing."
        )
    return type_value, parsed


def _is_pairing_response_frame(data: bytes) -> bool:
    """Recognize a complete plaintext pairing reply without decoding its payload."""

    if (
        len(data) < 16
        or data[6] != 0xAB
        or data[7] != 0
        or data[8] != 0
        or data[9] != 0
        or data[-1] != 0x54
    ):
        return False
    body_length = int.from_bytes(data[10:12], "little")
    type_value = data[13]
    minimum_body_length = 83 if type_value == 0x85 else 2
    return (
        int.from_bytes(data[4:6], "little") == len(data) - 6
        and type_value in PAIRING_RESPONSE_TYPES
        and body_length >= minimum_body_length
        and len(data) == 14 + body_length
        and data[-2] == crc8(data[7:-2])
    )


def _notification_callback(
    replies: asyncio.Queue[bytes],
    pairing_replies: asyncio.Queue[bytes] | None = None,
) -> Callable[[Any, bytearray], None]:
    """Keep bounded recent notifications and reserve a queue for pairing replies."""

    def notify(_sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        target = (
            pairing_replies
            if pairing_replies is not None and _is_pairing_response_frame(frame)
            else replies
        )
        if target.full():
            try:
                target.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            target.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    return notify


async def _connect(
    hass: HomeAssistant,
    address: str,
    *,
    max_attempts: int = 1,
    use_services_cache: bool = True,
    disconnected_callback: Callable[[BleakClient], None] | None = None,
) -> BleakClientWithServiceCache:
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
            BleakClientWithServiceCache,
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
    progress_callback: Callable[[SupportEvent], None] | None = None,
) -> PairingResult:
    """Pair Home Assistant after the user confirms on the physical hub."""

    replies: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_PAIRING_NOTIFICATIONS)
    pairing_replies: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_PAIRING_NOTIFICATIONS)
    last_polled_response = b""

    # A hub that has just restarted can advertise before its complete GATT
    # table is available through a proxy. Reconnect before asking the user for
    # approval; no pairing request has reached the hub at this point.
    client: BleakClientWithServiceCache | None = None
    try:
        last_service_error: BleakCharacteristicNotFoundError | None = None
        for service_attempt in range(3):
            client = await _connect(
                hass,
                address,
                max_attempts=3,
                use_services_cache=False,
            )
            try:
                # COMMAND and RESPONSE carry the pairing exchange. A built-in
                # controller may omit STATUS, NOTIFICATION and SESSION entirely;
                # their absence does not establish an incomplete service cache.
                for uuid in (COMMAND_UUID, RESPONSE_UUID):
                    if client.services.get_characteristic(uuid) is None:
                        raise BleakCharacteristicNotFoundError(uuid)

                # Each connection owns its queue. Notifications received during
                # failed service setup must not become the next link's greeting.
                replies = asyncio.Queue(maxsize=MAX_PAIRING_NOTIFICATIONS)
                pairing_replies = asyncio.Queue(maxsize=MAX_PAIRING_NOTIFICATIONS)
                notify = _notification_callback(replies, pairing_replies)

                for uuid in (RESPONSE_UUID, STATUS_UUID, NOTIFICATION_UUID):
                    characteristic = client.services.get_characteristic(uuid)
                    if characteristic is None or not {"notify", "indicate"}.intersection(
                        characteristic.properties
                    ):
                        continue
                    try:
                        await client.start_notify(uuid, notify)
                    except BleakCharacteristicNotFoundError:
                        raise
                    except Exception:
                        _LOGGER.debug(
                            "Hub characteristic %s does not notify",
                            uuid,
                            exc_info=True,
                        )
                if client.services.get_characteristic(SESSION_UUID) is not None:
                    await client.write_gatt_char(SESSION_UUID, b"\x01", response=True)
            except BleakCharacteristicNotFoundError as exc:
                last_service_error = exc
                # ESPHome remote caching can ignore use_services_cache=False.
                # Clear the actual GATT cache while connected, never discovery
                # history (which would discard the route needed for reconnect).
                try:
                    async with asyncio.timeout(5.0):
                        await client.clear_cache()
                except Exception:
                    _LOGGER.debug("Could not clear Weber GATT services cache", exc_info=True)
                await _safe_disconnect(client)
                client = None
                if service_attempt == 2:
                    continue
                _LOGGER.debug(
                    "Weber pairing services were incomplete; reconnecting (%s/3)",
                    service_attempt + 1,
                )
                await asyncio.sleep(float(service_attempt + 1))
                continue
            break
        else:
            raise WeberBluetoothError(
                "The hub connected, but its Bluetooth services were not ready. "
                "Wake the hub and try pairing again."
            ) from last_service_error

        if progress_callback is not None:
            progress_callback(SupportEvent.SERVICES)

        async def poll_response(timeout: float) -> bytes | None:
            nonlocal last_polled_response
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                try:
                    queued = pairing_replies.get_nowait()
                except asyncio.QueueEmpty:
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
            if progress_callback is not None:
                progress_callback(SupportEvent.HANDSHAKE)
            reply = await poll_response(10.0)
            if reply is None:
                continue
            try:
                type_value, parsed = _pairing_payload(reply)
            except WeberBluetoothTelemetryError:
                _LOGGER.warning("Ignored unauthenticated telemetry during Weber pairing")
                continue
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

        if progress_callback is not None:
            progress_callback(SupportEvent.REQUESTED)
        deadline = asyncio.get_running_loop().time() + confirmation_timeout
        pairing_payload: dict[str, Any] | None = None
        while asyncio.get_running_loop().time() < deadline:
            reply = await poll_response(min(2.0, deadline - asyncio.get_running_loop().time()))
            if reply is None:
                continue
            try:
                _type_value, parsed = _pairing_payload(reply)
            except WeberBluetoothTelemetryError:
                _LOGGER.warning("Ignored unauthenticated telemetry during Weber pairing")
                continue
            if isinstance(parsed, dict) and parsed.get("kind") == "pairing_response":
                pairing_payload = parsed
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
        if len(appliance_id) != 32:
            raise WeberBluetoothError("The hub returned an invalid appliance identity.")

        if progress_callback is not None:
            progress_callback(SupportEvent.CONFIRMED)
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
        )
    except BleakCharacteristicNotFoundError as exc:
        raise WeberBluetoothError(
            "The hub's Bluetooth services changed during pairing. "
            "Wake the hub and try pairing again."
        ) from exc
    finally:
        # Disconnecting a Bleak client also removes its notification callbacks.
        # Avoid extra GATT stop-notify traffic after a link has already dropped.
        if client is not None:
            await _safe_disconnect(client)

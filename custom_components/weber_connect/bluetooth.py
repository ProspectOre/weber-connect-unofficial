"""Weber GATT protocol over Home Assistant's local and proxy scanners."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import establish_connection
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


class WeberBluetoothError(RuntimeError):
    """A hub was unavailable or returned an invalid protocol response."""


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


async def _connect(hass: HomeAssistant, address: str) -> BleakClient:
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise WeberBluetoothError(
            "The hub is not reachable from any active Home Assistant Bluetooth adapter or proxy."
        )
    return await establish_connection(
        BleakClient,
        device,
        NAME,
        max_attempts=4,
        use_services_cache=True,
        ble_device_callback=lambda: (
            bluetooth.async_ble_device_from_address(hass, address, connectable=True) or device
        ),
    )


async def _safe_disconnect(client: BleakClient) -> None:
    try:
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

    client = await _connect(hass, address)
    replies: asyncio.Queue[bytes] = asyncio.Queue()
    last_polled_response = b""

    def notify(_sender: Any, data: bytearray) -> None:
        replies.put_nowait(bytes(data))

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

    subscribed: list[str] = []
    try:
        for uuid in (RESPONSE_UUID, STATUS_UUID, NOTIFICATION_UUID):
            try:
                await client.start_notify(uuid, notify)
                subscribed.append(uuid)
            except Exception:
                _LOGGER.debug("Hub characteristic %s does not notify", uuid, exc_info=True)
        await client.write_gatt_char(SESSION_UUID, b"\x01", response=True)

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
    finally:
        for uuid in subscribed:
            try:
                await client.stop_notify(uuid)
            except Exception:
                _LOGGER.debug("Could not stop notification %s", uuid, exc_info=True)
        await _safe_disconnect(client)


async def async_read_status(
    hass: HomeAssistant,
    address: str,
    companion_id: str,
    message_version: int,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Read one local status frame through the best HA adapter or proxy."""

    client = await _connect(hass, address)
    received = asyncio.Event()
    latest: dict[str, Any] | None = None

    def handler(_sender: Any, data: bytearray) -> None:
        nonlocal latest
        _type_value, parsed, _code = _payload(bytes(data))
        if isinstance(parsed, dict) and parsed.get("kind") == "cook_session_status":
            latest = parsed
            received.set()

    subscribed: list[str] = []
    try:
        for uuid in (STATUS_UUID, NOTIFICATION_UUID, RESPONSE_UUID):
            try:
                await client.start_notify(uuid, handler)
                subscribed.append(uuid)
            except Exception:
                _LOGGER.debug("Hub characteristic %s does not notify", uuid, exc_info=True)
        frame = build_command_frame(
            1,
            message_version,
            0x70,
            build_handshake_body(companion_id, secrets.token_bytes(32)),
        )
        await client.write_gatt_char(SESSION_UUID, frame, response=True)
        try:
            await asyncio.wait_for(received.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise WeberBluetoothError("The hub connected but did not return probe status.") from exc
        if latest is None:
            raise WeberBluetoothError("The hub returned an empty status response.")
        return latest
    finally:
        for uuid in subscribed:
            try:
                await client.stop_notify(uuid)
            except Exception:
                _LOGGER.debug("Could not stop notification %s", uuid, exc_info=True)
        await _safe_disconnect(client)

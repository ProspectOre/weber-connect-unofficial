"""Persistent read-only Weber companion WebSocket transport."""

from __future__ import annotations

import asyncio
import logging
import ssl
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .const import CLOUD_OFFLINE_RETAINED_KEYS
from .saber_frames import (
    parse_appliance_status_payload,
    parse_cook_session_status_payload,
    parse_tlv,
)
from .weber_cloud import WeberCloudAuthError

LOGGER = logging.getLogger(__name__)

SOCKET_PATH = "/2/messaging/websocket/companion"
ROUTING_HEADER_LENGTH = 35
TRANSPORT_HEADER_LENGTH = 6
MESSAGE_VERSION = 10
# Telemetry requests have no payload. Keep this boundary separate from the
# generic frame codec, which also handles incoming messages and protocol tools.
READ_ONLY_REQUEST_TYPES = frozenset({0x05, 0x07, 0x0E})
STATUS_INTERVAL = 10.0
STATUS_TIMEOUT = 12.0
FUEL_STATUS_MAX_AGE = 60.0
FUEL_STATUS_TAGS = {"fuel_level": 20, "fuel_percent": 27}
RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)

StatusCallback = Callable[[dict[str, Any]], None]
ErrorCallback = Callable[[str], None]


class WeberCloudSocketError(RuntimeError):
    """The companion WebSocket rejected or returned an invalid message."""


class WeberCloudCredentialError(WeberCloudSocketError):
    """The generated companion credential is no longer accepted."""


@dataclass(frozen=True, slots=True)
class RoutedMessage:
    """One decoded companion-routing envelope."""

    source_id: str
    target_id: str
    sequence: int
    message_version: int
    type_value: int
    payload: bytes


def decode_routed_message(data: bytes) -> RoutedMessage:
    """Decode Weber's routing, transport, and appliance headers."""

    minimum = ROUTING_HEADER_LENGTH + TRANSPORT_HEADER_LENGTH + 2
    if len(data) < minimum:
        raise WeberCloudSocketError("Cloud socket message is too short.")
    if data[0] != 1:
        raise WeberCloudSocketError(f"Unsupported routing version {data[0]}.")
    if data[1] not in {1, 2} or data[18] not in {1, 2}:
        raise WeberCloudSocketError("Cloud socket routing header is invalid.")
    source_id = data[2:18].hex()
    target_id = data[19:35].hex()
    sequence, length = struct.unpack_from("<IH", data, ROUTING_HEADER_LENGTH)
    body = data[ROUTING_HEADER_LENGTH + TRANSPORT_HEADER_LENGTH :]
    if len(body) != length:
        raise WeberCloudSocketError(
            f"Cloud socket transport length mismatch ({length} != {len(body)})."
        )
    return RoutedMessage(
        source_id=source_id,
        target_id=target_id,
        sequence=sequence,
        message_version=body[0],
        type_value=body[1],
        payload=body[2:],
    )


def encode_routed_message(
    companion_id: str,
    appliance_id: str,
    sequence: int,
    type_value: int,
    payload: bytes = b"",
    *,
    message_version: int = MESSAGE_VERSION,
) -> bytes:
    """Build the official v2 targeted-companion WebSocket envelope."""

    try:
        source = bytes.fromhex(companion_id)
        target = bytes.fromhex(appliance_id)
    except ValueError as exc:
        raise ValueError("Companion and appliance IDs must be hexadecimal.") from exc
    if len(source) != 16 or len(target) != 16:
        raise ValueError("Companion and appliance IDs must each be 16 bytes.")
    appliance = bytes([message_version & 0xFF, type_value & 0xFF]) + payload
    routing = bytes([1, 2]) + source + bytes([2]) + target
    transport = struct.pack("<IH", sequence & 0xFFFFFFFF, len(appliance))
    return routing + transport + appliance


class WeberCloudSession:
    """Own one asynchronous companion WebSocket for a config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        cloud_client: Any,
        appliance_id: str,
        *,
        timeout: float = STATUS_TIMEOUT,
        request_timeout: float = 30.0,
        subscribe_delay: float = 0.05,
    ) -> None:
        self.hass = hass
        self.cloud_client = cloud_client
        self.appliance_id = appliance_id
        self.timeout = timeout
        self.request_timeout = request_timeout
        self.subscribe_delay = subscribe_delay
        self._connection: ClientConnection | None = None
        self._wake_future: asyncio.Future[Any] | None = None
        self._sequence = 1
        self._subscribed = False
        self._closed = False
        self._wake = asyncio.Event()
        self.received_types: list[int] = []
        self.error_kind = "connection"
        self.last_error_type: str | None = None
        self.socket_connections = 0
        self.fast_recoveries = 0
        self._appliance_status: dict[str, Any] = {}
        self._fuel_sample_times: dict[str, float] = {}
        self.fuel_level_reports = 0
        self.fuel_level_unknown_reports = 0
        self.fuel_level_changes = 0
        self._last_reported_fuel_level: str | None = None
        self._recovery_started_at: float | None = None
        self.last_recovery_seconds: float | None = None
        self.max_recovery_seconds: float | None = None
        self.fuel_level_code_counts = {
            **{str(code): 0 for code in range(6)},
            "reserved": 0,
        }
        self.fuel_level_invalid_field_count = 0

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence = 1 if value >= 0xFFFFFFFF else value + 1
        return value

    async def _async_connect(self) -> ClientConnection:
        connection = self._connection
        if connection is not None:
            return connection
        try:
            token = await self.hass.async_add_executor_job(self.cloud_client.token)
        except WeberCloudAuthError as exc:
            raise WeberCloudCredentialError(
                "Weber rejected Home Assistant's private companion credential."
            ) from exc
        try:
            await self._async_wake_messaging()
        except WeberCloudAuthError as exc:
            raise WeberCloudCredentialError(
                "Weber rejected Home Assistant's private companion credential."
            ) from exc
        except Exception:
            LOGGER.debug("Cloud messaging wake-up failed", exc_info=True)
        ssl_context = await self.hass.async_add_executor_job(ssl.create_default_context)
        try:
            self._connection = await connect(
                f"wss://{self.cloud_client.messaging_host}{SOCKET_PATH}",
                additional_headers={"Authorization": f"Bearer {token}"},
                user_agent_header=self.cloud_client.user_agent,
                open_timeout=self.timeout,
                ping_interval=40,
                ping_timeout=20,
                close_timeout=3,
                compression=None,
                max_size=1024 * 1024,
                proxy=None,
                ssl=ssl_context,
            )
        except InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403}:
                raise WeberCloudCredentialError(
                    "Weber rejected Home Assistant's private companion credential."
                ) from exc
            raise
        self._subscribed = False
        self.socket_connections += 1
        return self._connection

    async def _async_wake_messaging(self) -> None:
        """Share a still-running executor wake request after async cancellation."""

        wake_future = self._wake_future
        if wake_future is None:
            wake_future = asyncio.ensure_future(
                self.hass.async_add_executor_job(
                    self.cloud_client.wake_messaging,
                    self.appliance_id,
                )
            )
            self._wake_future = wake_future

            def clear_wake(done: asyncio.Future[Any]) -> None:
                if self._wake_future is done:
                    self._wake_future = None
                if not done.cancelled():
                    done.exception()

            wake_future.add_done_callback(clear_wake)
        await asyncio.shield(wake_future)

    async def _async_close_connection(self) -> None:
        connection, self._connection = self._connection, None
        self._subscribed = False
        # Cook status may arrive before appliance status after a reconnect.
        # Bridge brief interruptions with fuel sampled within the last minute;
        # expiry is also checked on every publication, never extended by reuse.
        # Device cloud status must be reported again on the new connection.
        self._expire_fuel_status()
        self._appliance_status = {
            key: self._appliance_status[key]
            for key in (*CLOUD_OFFLINE_RETAINED_KEYS, *self._fuel_sample_times)
            if key in self._appliance_status
        }
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                LOGGER.debug("Could not close cloud socket cleanly", exc_info=True)

    def _expire_fuel_status(self) -> None:
        """Do not let cook-only or partial frames perpetuate stale fuel."""

        for key, sampled_at in tuple(self._fuel_sample_times.items()):
            if time.monotonic() - sampled_at >= FUEL_STATUS_MAX_AGE:
                self._appliance_status.pop(key, None)
                del self._fuel_sample_times[key]

    async def _async_send(self, type_value: int, payload: bytes = b"") -> None:
        # Reject before connecting, waking the hub, or allocating a sequence.
        # In particular, settings and program-detail requests are not telemetry.
        if (
            type(type_value) is not int
            or type(payload) is not bytes
            or type_value not in READ_ONLY_REQUEST_TYPES
            or payload != b""
        ):
            raise WeberCloudSocketError("Only empty-payload telemetry requests are permitted.")
        connection = await self._async_connect()
        await connection.send(
            encode_routed_message(
                self.cloud_client.config.device_id,
                self.appliance_id,
                self._next_sequence(),
                type_value,
                payload,
            )
        )

    async def _async_subscribe(self) -> None:
        """Initialize telemetry using only capability and current-status reads."""

        requests = (
            (0x0E, b""),
            (0x05, b""),
            (0x07, b""),
            (0x0E, b""),
            (0x05, b""),
            (0x07, b""),
        )
        for type_value, payload in requests:
            await self._async_send(type_value, payload)
            if self.subscribe_delay:
                await asyncio.sleep(self.subscribe_delay)
        self._subscribed = True

    async def _async_receive_status(self) -> dict[str, Any]:
        connection = await self._async_connect()
        async with asyncio.timeout(self.timeout):
            while True:
                raw = await connection.recv()
                if not isinstance(raw, bytes):
                    continue
                message = decode_routed_message(raw)
                if (
                    message.source_id != self.appliance_id.lower()
                    or message.target_id != self.cloud_client.config.device_id.lower()
                ):
                    raise WeberCloudSocketError(
                        "Cloud socket message was routed to or from an unexpected device."
                    )
                self.received_types = [*self.received_types[-19:], message.type_value]
                if message.type_value == 0x87:
                    raise WeberCloudSocketError("The hub rejected the cloud request.")
                if message.type_value == 0x83:
                    fields = parse_tlv(message.payload)
                    if -1 in fields:
                        raise WeberCloudSocketError(
                            "Cloud appliance-status TLV container is malformed."
                        )
                    appliance_status = parse_appliance_status_payload(message.payload)
                    fuel_fields = fields.get(20, [])
                    if fuel_fields:
                        self.fuel_level_reports += 1
                        if len(fuel_fields) != 1 or len(fuel_fields[0]) != 1:
                            self.fuel_level_invalid_field_count += 1
                            appliance_status["fuel_level"] = None
                        else:
                            fuel_code = fuel_fields[0][0]
                            bucket = str(fuel_code) if fuel_code <= 5 else "reserved"
                            self.fuel_level_code_counts[bucket] += 1
                            # A present, one-byte code always maps to a state:
                            # known values map directly and all others to unknown.
                            fuel_level = appliance_status["fuel_level"]
                            if fuel_level == "unknown":
                                self.fuel_level_unknown_reports += 1
                            if (
                                self._last_reported_fuel_level is not None
                                and fuel_level != self._last_reported_fuel_level
                            ):
                                self.fuel_level_changes += 1
                            self._last_reported_fuel_level = fuel_level
                    for key, tag in FUEL_STATUS_TAGS.items():
                        values = fields.get(tag, [])
                        value = appliance_status[key]
                        if (
                            len(values) == 1
                            and len(values[0]) == 1
                            and value is not None
                            and (key != "fuel_percent" or 0 <= value <= 100)
                        ):
                            self._fuel_sample_times[key] = time.monotonic()
                        else:
                            appliance_status[key] = None
                    # Weber can send partial appliance-status frames between full
                    # snapshots. Missing TLVs parse as None, but they mean "not
                    # included in this frame", not that every omitted entity has
                    # become unknown. Merge only reported values so one partial
                    # frame cannot briefly clear all hub-level telemetry.
                    self._appliance_status.update(
                        {
                            key: value
                            for key, value in appliance_status.items()
                            if key not in {"kind", "unparsed_tail_hex"} and value is not None
                        }
                    )
                    continue
                if message.type_value == 0x80:
                    self._expire_fuel_status()
                    cook_status = parse_cook_session_status_payload(message.payload)
                    if self._recovery_started_at is not None:
                        recovery_seconds = max(0.0, time.monotonic() - self._recovery_started_at)
                        self.last_recovery_seconds = recovery_seconds
                        self.max_recovery_seconds = max(
                            self.max_recovery_seconds or 0.0, recovery_seconds
                        )
                        self._recovery_started_at = None
                    return {
                        **cook_status,
                        **{
                            key: value
                            for key, value in self._appliance_status.items()
                            if key != "kind"
                        },
                    }

    async def async_request_status(self) -> dict[str, Any]:
        """Request one current status without rebuilding the socket."""

        previous_connection = self._connection
        async with asyncio.timeout(self.request_timeout):
            try:
                return await self._async_request_status_once()
            except ConnectionClosed, OSError, TimeoutError:
                # Only an already-established socket gets one immediate fresh
                # connection attempt. The same-socket resubscribe path is
                # intentionally absent so it cannot stack another read window.
                if previous_connection is None or self._connection is not previous_connection:
                    raise
                await self._async_close_connection()
                status = await self._async_request_status_once()
                self.fast_recoveries += 1
                return status

    async def _async_request_status_once(self) -> dict[str, Any]:
        """Send a status request and wait for a fresh cook-status frame."""

        if self._connection is not None and await self.hass.async_add_executor_job(
            self.cloud_client.token_needs_refresh
        ):
            # Weber bearer tokens expire after roughly 5.8 hours. Rotate the
            # socket before sending again so a long-lived session never relies
            # on an expired upgrade credential.
            await self._async_close_connection()
        if not self._subscribed:
            await self._async_subscribe()
        else:
            await self._async_send(0x07)
            await self._async_send(0x05)
        return await self._async_receive_status()

    async def async_run(
        self,
        status_callback: StatusCallback,
        error_callback: ErrorCallback,
    ) -> None:
        """Keep the socket alive and publish status at the live cadence."""

        delay_index = 0
        try:
            while not self._closed:
                # Preserve a wake request that arrives while network I/O is in
                # progress so the next attempt starts immediately.
                self._wake.clear()
                started = asyncio.get_running_loop().time()
                try:
                    status = await self.async_request_status()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._async_close_connection()
                    if self._recovery_started_at is None:
                        self._recovery_started_at = time.monotonic()
                    self.error_kind = (
                        "credentials"
                        if isinstance(exc, WeberCloudCredentialError)
                        else "connection"
                    )
                    self.last_error_type = type(exc).__name__
                    detail = str(exc) or "No response before the connection deadline"
                    error_callback(
                        f"Weber Cloud connection failed ({self.last_error_type}): {detail}"
                    )
                    delay = RECONNECT_DELAYS[min(delay_index, len(RECONNECT_DELAYS) - 1)]
                    delay_index += 1
                else:
                    self.error_kind = "connection"
                    status_callback(status)
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
            await self.async_close()

    def async_wake(self) -> None:
        """Request an immediate cloud retry."""

        self._wake.set()

    async def async_close(self) -> None:
        """Close the config-entry-owned companion socket."""

        self._closed = True
        self._wake.set()
        await self._async_close_connection()

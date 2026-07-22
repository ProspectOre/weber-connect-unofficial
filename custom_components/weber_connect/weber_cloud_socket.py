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
from websockets.exceptions import InvalidStatus

from .saber_frames import parse_cook_session_status_payload
from .weber_cloud import WeberCloudAuthError

LOGGER = logging.getLogger(__name__)

SOCKET_PATH = "/2/messaging/websocket/companion"
ROUTING_HEADER_LENGTH = 35
TRANSPORT_HEADER_LENGTH = 6
MESSAGE_VERSION = 10
STATUS_INTERVAL = 10.0
STATUS_TIMEOUT = 12.0
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
        subscribe_delay: float = 0.05,
    ) -> None:
        self.hass = hass
        self.cloud_client = cloud_client
        self.appliance_id = appliance_id
        self.timeout = timeout
        self.subscribe_delay = subscribe_delay
        self._connection: ClientConnection | None = None
        self._sequence = 1
        self._subscribed = False
        self._closed = False
        self._wake = asyncio.Event()
        self.received_types: list[int] = []
        self.error_kind = "connection"

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
            await self.hass.async_add_executor_job(
                self.cloud_client.wake_messaging,
                self.appliance_id,
            )
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
        return self._connection

    async def _async_close_connection(self) -> None:
        connection, self._connection = self._connection, None
        self._subscribed = False
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                LOGGER.debug("Could not close cloud socket cleanly", exc_info=True)

    async def _async_send(self, type_value: int, payload: bytes = b"") -> None:
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
        """Send the observed initial subscription used by the companion app."""

        now = int(time.time())

        def timestamp(value: int) -> bytes:
            return bytes([0x15, 0x04]) + struct.pack("<I", value)

        requests = (
            (0x0E, b""),
            (0x05, b""),
            (0x09, timestamp(now)),
            (0x07, b""),
            (0x0B, b"\x01\x00"),
            (0x0E, b""),
            (0x09, timestamp(now + 1)),
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
                if message.type_value == 0x80:
                    return parse_cook_session_status_payload(message.payload)

    async def async_request_status(self) -> dict[str, Any]:
        """Request one current status without rebuilding the socket."""

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
            await self._async_send(0x05)
        try:
            return await self._async_receive_status()
        except TimeoutError:
            # A long-idle relay can forget the subscription while retaining the
            # TCP socket. Renew it once before treating the connection as lost.
            self._subscribed = False
            await self._async_subscribe()
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
                    self.error_kind = (
                        "credentials"
                        if isinstance(exc, WeberCloudCredentialError)
                        else "connection"
                    )
                    error_callback(f"Weber Cloud connection failed: {exc}")
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

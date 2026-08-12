"""Persistent asynchronous companion WebSocket contracts."""

from __future__ import annotations

import asyncio
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.weber_connect import weber_cloud_socket as socket
from custom_components.weber_connect.weber_cloud import WeberCloudAuthError

DEVICE_ID = "11" * 16
APPLIANCE_ID = "22" * 16


def routed(type_value: int, payload: bytes = b"") -> bytes:
    return socket.encode_routed_message(
        APPLIANCE_ID,
        DEVICE_ID,
        7,
        type_value,
        payload,
    )


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag, len(value)]) + value


class FakeHass:
    async def async_add_executor_job(self, target: object, *args: object) -> object:
        return target(*args)  # type: ignore[operator]


class FakeCloudClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(device_id=DEVICE_ID)
        self.messaging_host = "messaging.example"
        self.user_agent = "test-agent"
        self.wake_calls: list[str] = []
        self.token_calls = 0
        self.refresh_required = False

    def token(self) -> str:
        self.token_calls += 1
        self.refresh_required = False
        return "token"

    def token_needs_refresh(self) -> bool:
        return self.refresh_required

    def wake_messaging(self, appliance_id: str) -> None:
        self.wake_calls.append(appliance_id)


class FakeConnection:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> object:
        if self.responses:
            response = self.responses.pop(0)
            if response is None:
                await asyncio.sleep(3600)
            if isinstance(response, Exception):
                raise response
            return response
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


def test_routed_message_round_trip_and_validation() -> None:
    encoded = socket.encode_routed_message(DEVICE_ID, APPLIANCE_ID, 9, 0x80, b"abc")
    decoded = socket.decode_routed_message(encoded)
    assert decoded.source_id == DEVICE_ID
    assert decoded.target_id == APPLIANCE_ID
    assert decoded.sequence == 9
    assert decoded.type_value == 0x80
    assert decoded.payload == b"abc"

    with pytest.raises(ValueError, match="hexadecimal"):
        socket.encode_routed_message("bad", APPLIANCE_ID, 1, 1)
    with pytest.raises(ValueError, match="16 bytes"):
        socket.encode_routed_message("11", APPLIANCE_ID, 1, 1)
    with pytest.raises(socket.WeberCloudSocketError, match="too short"):
        socket.decode_routed_message(b"")

    invalid_version = bytearray(encoded)
    invalid_version[0] = 3
    with pytest.raises(socket.WeberCloudSocketError, match="routing version"):
        socket.decode_routed_message(bytes(invalid_version))
    invalid_route = bytearray(encoded)
    invalid_route[1] = 9
    with pytest.raises(socket.WeberCloudSocketError, match="routing header"):
        socket.decode_routed_message(bytes(invalid_route))
    with pytest.raises(socket.WeberCloudSocketError, match="length mismatch"):
        socket.decode_routed_message(encoded + b"x")


@pytest.mark.asyncio
async def test_first_status_subscribes_once_and_next_status_reuses_socket() -> None:
    connection = FakeConnection([routed(0x80), "ignore", routed(0x80)])
    cloud = FakeCloudClient()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)) as connect:
        first = await session.async_request_status()
        initial_count = len(connection.sent)
        second = await session.async_request_status()

    assert first["kind"] == "cook_session_status"
    assert second["kind"] == "cook_session_status"
    assert initial_count == 9
    assert len(connection.sent) == 11
    assert socket.decode_routed_message(connection.sent[-2]).type_value == 0x07
    assert socket.decode_routed_message(connection.sent[-1]).type_value == 0x05
    assert cloud.wake_calls == [APPLIANCE_ID]
    connect.assert_awaited_once()
    assert connect.await_args.kwargs["proxy"] is None
    assert isinstance(connect.await_args.kwargs["ssl"], ssl.SSLContext)
    assert session.received_types == [0x80, 0x80]


@pytest.mark.asyncio
async def test_appliance_status_is_merged_into_cook_status() -> None:
    connection = FakeConnection(
        [
            routed(0x83, tlv(1, bytes([64])) + tlv(2, bytes([1]))),
            routed(0x80),
        ]
    )
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        status = await session.async_request_status()

    assert status["kind"] == "cook_session_status"
    assert status["battery_level"] == 64
    assert status["is_charging"] is True
    assert session.received_types == [0x83, 0x80]


@pytest.mark.asyncio
async def test_partial_appliance_status_preserves_cached_hub_telemetry() -> None:
    connection = FakeConnection(
        [
            routed(
                0x83,
                tlv(1, bytes([64]))
                + tlv(2, bytes([1]))
                + tlv(10, (-61).to_bytes(4, "little", signed=True))
                + tlv(15, bytes([2])),
            ),
            routed(0x80),
            routed(0x83, tlv(10, (-52).to_bytes(4, "little", signed=True))),
            routed(0x80),
        ]
    )
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        first = await session.async_request_status()
        second = await session.async_request_status()

    assert first["battery_level"] == 64
    assert first["is_charging"] is True
    assert first["wifi_signal_strength"] == -61
    assert first["wifi_connection_status"] == "connected"
    assert second["battery_level"] == 64
    assert second["is_charging"] is True
    assert second["wifi_signal_strength"] == -52
    assert second["wifi_connection_status"] == "connected"
    assert session.received_types == [0x83, 0x80, 0x83, 0x80]


@pytest.mark.asyncio
async def test_idle_socket_renews_subscription_once_before_failure() -> None:
    connection = FakeConnection([None, routed(0x80)])
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.01,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        status = await session.async_request_status()
    assert status["kind"] == "cook_session_status"
    assert len(connection.sent) == 18


@pytest.mark.asyncio
async def test_expiring_token_rotates_socket_before_next_request() -> None:
    first = FakeConnection([routed(0x80)])
    second = FakeConnection([routed(0x80)])
    cloud = FakeCloudClient()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])) as connect:
        await session.async_request_status()
        cloud.refresh_required = True
        await session.async_request_status()

    assert first.closed is True
    assert len(first.sent) == 9
    assert len(second.sent) == 9
    assert cloud.token_calls == 2
    assert connect.await_count == 2


@pytest.mark.asyncio
async def test_reconnect_preserves_cached_appliance_status() -> None:
    first = FakeConnection(
        [
            routed(
                0x83,
                tlv(1, bytes([64]))
                + tlv(2, bytes([1]))
                + tlv(10, (-61).to_bytes(4, "little", signed=True))
                + tlv(15, bytes([2])),
            ),
            routed(0x80),
        ]
    )
    # A newly subscribed socket can deliver cook status before the first fresh
    # appliance-status frame. The previous complete snapshot must bridge that
    # reconnect instead of briefly publishing unknown hub-level telemetry.
    second = FakeConnection([routed(0x80)])
    cloud = FakeCloudClient()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])):
        before_reconnect = await session.async_request_status()
        cloud.refresh_required = True
        after_reconnect = await session.async_request_status()

    assert before_reconnect["battery_level"] == 64
    assert after_reconnect["battery_level"] == 64
    assert after_reconnect["is_charging"] is True
    assert after_reconnect["wifi_signal_strength"] == -61
    assert after_reconnect["wifi_connection_status"] == "connected"
    assert first.closed is True
    assert session.received_types == [0x83, 0x80, 0x80]


@pytest.mark.asyncio
async def test_text_frames_are_ignored_and_rejection_is_actionable() -> None:
    connection = FakeConnection(["text", routed(0x87)])
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        with pytest.raises(socket.WeberCloudSocketError, match="rejected"):
            await session.async_request_status()


@pytest.mark.asyncio
async def test_status_rejects_wrong_source_or_target_route() -> None:
    wrong_source = socket.encode_routed_message("33" * 16, DEVICE_ID, 7, 0x80)
    connection = FakeConnection([wrong_source])
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        with pytest.raises(socket.WeberCloudSocketError, match="unexpected device"):
            await session.async_request_status()


@pytest.mark.asyncio
async def test_rejected_companion_token_is_classified_for_repair() -> None:
    cloud = FakeCloudClient()
    cloud.token = MagicMock(side_effect=WeberCloudAuthError("HTTP 403"))  # type: ignore[method-assign]
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )

    with pytest.raises(socket.WeberCloudCredentialError, match="rejected"):
        await session.async_request_status()


@pytest.mark.asyncio
async def test_rejected_wake_or_websocket_upgrade_is_classified() -> None:
    cloud = FakeCloudClient()
    cloud.wake_messaging = MagicMock(  # type: ignore[method-assign]
        side_effect=WeberCloudAuthError("HTTP 403")
    )
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with pytest.raises(socket.WeberCloudCredentialError, match="rejected"):
        await session.async_request_status()

    cloud = FakeCloudClient()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    rejected_upgrade = socket.InvalidStatus(SimpleNamespace(status_code=401))
    with patch.object(socket, "connect", AsyncMock(side_effect=rejected_upgrade)):
        with pytest.raises(socket.WeberCloudCredentialError, match="rejected"):
            await session.async_request_status()


@pytest.mark.asyncio
async def test_run_publishes_status_and_cancellation_closes_socket() -> None:
    connection = FakeConnection([routed(0x80)])
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    statuses: list[dict[str, object]] = []
    errors: list[str] = []
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        task = asyncio.create_task(session.async_run(statuses.append, errors.append))
        for _ in range(20):
            if statuses:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert statuses[0]["kind"] == "cook_session_status"
    assert errors == []
    assert connection.closed is True


@pytest.mark.asyncio
async def test_run_normalizes_connection_error_and_reconnects() -> None:
    connection = FakeConnection()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.01,
        subscribe_delay=0,
    )
    errors: list[str] = []
    connect = AsyncMock(side_effect=[OSError("offline"), connection])
    with patch.object(socket, "connect", connect):
        task = asyncio.create_task(session.async_run(MagicMock(), errors.append))
        for _ in range(20):
            if errors:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert "offline" in errors[0]
    assert session._connection is None


@pytest.mark.asyncio
async def test_close_is_idempotent_and_wake_interrupts_delay() -> None:
    connection = FakeConnection()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
    )
    session._connection = connection  # type: ignore[assignment]
    session.async_wake()
    assert session._wake.is_set()
    await session.async_close()
    await session.async_close()
    assert connection.closed is True

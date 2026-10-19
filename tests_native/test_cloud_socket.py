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
async def test_fuel_counters_only_track_present_appliance_fuel_tlvs() -> None:
    connection = FakeConnection(
        [
            routed(0x80),  # No appliance frame or fuel report.
            routed(0x83, tlv(1, bytes([64]))),  # Partial frame omits fuel.
            routed(0x80),
            routed(0x83, tlv(20, bytes([0]))),  # Explicitly unknown.
            routed(0x80),
            routed(0x80),  # Missing fuel does not repeat cached state.
            routed(0x83, tlv(20, bytes([1]))),  # Full.
            routed(0x83, tlv(20, bytes([5]))),  # Low.
            routed(0x80),
            routed(0x83, tlv(20, b"")),  # Present, but unusable.
            routed(0x80),
            routed(0x80),  # Missing TLV retains the last valid state.
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
        statuses = [await session.async_request_status() for _ in range(7)]

    assert "fuel_level" not in statuses[0]
    assert statuses[2]["fuel_level"] == "unknown"
    assert statuses[3]["fuel_level"] == "unknown"
    assert statuses[4]["fuel_level"] == "low"
    assert statuses[5]["fuel_level"] == "low"
    assert statuses[6]["fuel_level"] == "low"
    assert session.fuel_level_reports == 4
    assert session.fuel_level_unknown_reports == 1
    assert session.fuel_level_changes == 2
    assert session.fuel_level_code_counts["0"] == 1
    assert session.fuel_level_code_counts["1"] == 1
    assert session.fuel_level_code_counts["5"] == 1
    assert session.fuel_level_invalid_field_count == 1


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
async def test_invalid_fuel_tlv_shapes_do_not_replace_last_valid_fuel() -> None:
    connection = FakeConnection(
        [
            routed(0x83, tlv(1, b"\x3c") + tlv(20, b"\x01")),
            routed(0x80),
            routed(0x83, tlv(1, b"\x46") + tlv(20, b"\x00\x01")),
            routed(0x80),
            routed(0x83, tlv(1, b"\x50") + tlv(20, b"\x00") + tlv(20, b"\x05")),
            routed(0x80),
            routed(0x83, tlv(20, b"\x06")),  # Reserved code remains explicit unknown.
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
        valid = await session.async_request_status()
        wrong_width = await session.async_request_status()
        duplicate = await session.async_request_status()
        reserved = await session.async_request_status()

    assert valid["fuel_level"] == "full"
    assert wrong_width["fuel_level"] == "full"
    assert wrong_width["battery_level"] == 70
    assert duplicate["fuel_level"] == "full"
    assert duplicate["battery_level"] == 80
    assert reserved["fuel_level"] == "unknown"
    assert session.fuel_level_reports == 4
    assert session.fuel_level_invalid_field_count == 2
    assert session.fuel_level_unknown_reports == 1
    assert session.fuel_level_changes == 1
    assert session.fuel_level_code_counts == {
        "0": 0,
        "1": 1,
        "2": 0,
        "3": 0,
        "4": 0,
        "5": 0,
        "reserved": 1,
    }


@pytest.mark.asyncio
async def test_malformed_appliance_tlv_container_fails_before_cache_mutation() -> None:
    valid_frame = tlv(1, b"\x40") + tlv(20, b"\x05")
    malformed_frame = tlv(1, b"\x50") + tlv(20, b"\x01") + bytes((10, 3, 0xAA))
    connection = FakeConnection(
        [routed(0x83, valid_frame), routed(0x80), routed(0x83, malformed_frame)]
    )
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        valid = await session.async_request_status()
        with pytest.raises(socket.WeberCloudSocketError, match="TLV container is malformed"):
            await session.async_request_status()

    assert valid["battery_level"] == 64
    assert valid["fuel_level"] == "low"
    assert session._appliance_status["battery_level"] == 64
    assert session._appliance_status["fuel_level"] == "low"
    assert session.fuel_level_reports == 1
    assert session.fuel_level_code_counts["5"] == 1
    assert session.fuel_level_invalid_field_count == 0


@pytest.mark.asyncio
async def test_silent_established_socket_recovers_on_one_fresh_connection() -> None:
    first = FakeConnection([routed(0x80), None])
    second = FakeConnection([routed(0x80)])
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.01,
        request_timeout=0.2,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])) as connect:
        await session.async_request_status()
        status = await session.async_request_status()
    assert status["kind"] == "cook_session_status"
    assert first.closed
    assert len(first.sent) == 11
    assert len(second.sent) == 9
    assert connect.await_count == 2
    assert session.fast_recoveries == 1


@pytest.mark.asyncio
async def test_request_budget_bounds_a_stalled_connect() -> None:
    gate = asyncio.Event()

    async def stalled_connect(*_args: object, **_kwargs: object) -> FakeConnection:
        await gate.wait()
        return FakeConnection([routed(0x80)])

    session = socket.WeberCloudSession(
        FakeHass(),
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=1.0,
        request_timeout=0.02,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=stalled_connect)) as connect:
        with pytest.raises(TimeoutError):
            await session.async_request_status()
    connect.assert_awaited_once()
    assert session._connection is None


@pytest.mark.asyncio
async def test_request_budget_bounds_stalled_send_and_receive() -> None:
    gate = asyncio.Event()
    connection = FakeConnection()

    async def stalled_io(*_args: object, **_kwargs: object) -> object:
        await gate.wait()
        return None

    connection.send = AsyncMock(side_effect=stalled_io)  # type: ignore[method-assign]
    session = socket.WeberCloudSession(
        FakeHass(),
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=1.0,
        request_timeout=0.02,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)) as connect:
        with pytest.raises(TimeoutError):
            await session.async_request_status()
    connect.assert_awaited_once()
    await session.async_close()
    assert connection.closed

    connection = FakeConnection()
    connection.recv = AsyncMock(side_effect=stalled_io)  # type: ignore[method-assign]
    session = socket.WeberCloudSession(
        FakeHass(),
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=1.0,
        request_timeout=0.02,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)) as connect:
        with pytest.raises(TimeoutError):
            await session.async_request_status()
    connect.assert_awaited_once()
    await session.async_close()
    assert connection.closed


@pytest.mark.asyncio
async def test_cancelled_status_run_closes_stalled_socket() -> None:
    started = asyncio.Event()
    gate = asyncio.Event()
    connection = FakeConnection()

    async def stalled_recv() -> object:
        started.set()
        await gate.wait()
        return None

    connection.recv = stalled_recv  # type: ignore[method-assign]
    session = socket.WeberCloudSession(
        FakeHass(),
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=1.0,
        request_timeout=1.0,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        task = asyncio.create_task(session.async_run(MagicMock(), MagicMock()))
        await asyncio.wait_for(started.wait(), timeout=0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert connection.closed
    assert session._connection is None


@pytest.mark.asyncio
async def test_timed_out_wake_job_is_shared_instead_of_duplicated() -> None:
    class BlockingWakeHass(FakeHass):
        def __init__(self) -> None:
            self.wake_started = asyncio.Event()
            self.release_wake = asyncio.Event()
            self.wake_job_count = 0

        async def async_add_executor_job(self, target: object, *args: object) -> object:
            if getattr(target, "__name__", "") == "wake_messaging":
                self.wake_job_count += 1
                self.wake_started.set()
                await self.release_wake.wait()
            return target(*args)  # type: ignore[operator]

    hass = BlockingWakeHass()
    session = socket.WeberCloudSession(
        hass,  # type: ignore[arg-type]
        FakeCloudClient(),
        APPLIANCE_ID,
        request_timeout=0.02,
        subscribe_delay=0,
    )
    with pytest.raises(TimeoutError):
        await session.async_request_status()
    await asyncio.wait_for(hass.wake_started.wait(), timeout=0.1)

    with pytest.raises(TimeoutError):
        await session.async_request_status()
    assert hass.wake_job_count == 1

    pending_wake = session._wake_future
    assert pending_wake is not None
    hass.release_wake.set()
    await pending_wake
    await asyncio.sleep(0)
    assert hass.wake_job_count == 1
    assert session.cloud_client.wake_calls == [APPLIANCE_ID]
    assert session._wake_future is None


@pytest.mark.asyncio
async def test_completed_old_wake_does_not_clear_replacement_future() -> None:
    class DeferredHass(FakeHass):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def async_add_executor_job(self, target: object, *args: object) -> object:
            self.started.set()
            await self.release.wait()
            return target(*args)  # type: ignore[operator]

    hass = DeferredHass()
    session = socket.WeberCloudSession(hass, FakeCloudClient(), APPLIANCE_ID)  # type: ignore[arg-type]
    caller = asyncio.create_task(session._async_wake_messaging())
    await asyncio.wait_for(hass.started.wait(), timeout=0.1)
    old_future = session._wake_future
    assert old_future is not None
    replacement = asyncio.get_running_loop().create_future()
    session._wake_future = replacement

    hass.release.set()
    await caller
    await asyncio.sleep(0)

    assert old_future.done()
    assert old_future.exception() is None
    assert session._wake_future is replacement
    replacement.cancel()


@pytest.mark.asyncio
async def test_cancelled_wake_future_is_cleared_without_reading_exception() -> None:
    class DeferredHass(FakeHass):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def async_add_executor_job(self, target: object, *args: object) -> object:
            self.started.set()
            await self.release.wait()
            return target(*args)  # type: ignore[operator]

    hass = DeferredHass()
    session = socket.WeberCloudSession(hass, FakeCloudClient(), APPLIANCE_ID)  # type: ignore[arg-type]
    caller = asyncio.create_task(session._async_wake_messaging())
    await asyncio.wait_for(hass.started.wait(), timeout=0.1)
    wake_future = session._wake_future
    assert wake_future is not None
    wake_future.cancel()

    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.sleep(0)

    assert session._wake_future is None


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
async def test_recovery_timer_waits_for_fresh_cook_status() -> None:
    connection = FakeConnection([routed(0x83, tlv(1, bytes([64]))), None])
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, timeout=0.01, subscribe_delay=0
    )
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        await session._async_connect()
        session._recovery_started_at = 10.0
        with pytest.raises(TimeoutError):
            await session._async_receive_status()
        assert session.last_recovery_seconds is None
        assert session.max_recovery_seconds is None

        connection.responses.append(routed(0x80))
        with patch.object(socket.time, "monotonic", return_value=25.0):
            status = await session._async_receive_status()

    assert status["kind"] == "cook_session_status"
    assert session.last_recovery_seconds == 15.0
    assert session.max_recovery_seconds == 15.0
    assert session._recovery_started_at is None


@pytest.mark.asyncio
async def test_reconnect_preserves_cached_appliance_status() -> None:
    first = FakeConnection(
        [
            routed(
                0x83,
                tlv(1, bytes([64]))
                + tlv(2, bytes([1]))
                + tlv(4, bytes([3]))
                + tlv(10, (-61).to_bytes(4, "little", signed=True))
                + tlv(14, b"rev-b")
                + tlv(15, bytes([2]))
                + tlv(17, bytes([1]))
                + tlv(19, b"2.0.3_7398")
                + tlv(20, bytes([5]))
                + tlv(27, bytes([18])),
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
    assert "cloud_connection_status" not in after_reconnect
    assert after_reconnect["device_state"] == "active"
    assert before_reconnect["fuel_level"] == "low"
    assert before_reconnect["fuel_percent"] == 18
    assert after_reconnect["fuel_level"] == "low"
    assert after_reconnect["fuel_percent"] == 18
    assert "software_version" not in after_reconnect
    assert "hardware_version" not in after_reconnect
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
    with patch.object(socket, "connect", AsyncMock(return_value=connection)) as connect:
        with pytest.raises(socket.WeberCloudSocketError, match="rejected"):
            await session.async_request_status()
    connect.assert_awaited_once()


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

    with patch.object(socket, "connect", AsyncMock()) as connect:
        with pytest.raises(socket.WeberCloudCredentialError, match="rejected"):
            await session.async_request_status()
    connect.assert_not_awaited()


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
    with patch.object(socket, "connect", AsyncMock()) as connect:
        with pytest.raises(socket.WeberCloudCredentialError, match="rejected"):
            await session.async_request_status()
    connect.assert_not_awaited()

    cloud = FakeCloudClient()
    session = socket.WeberCloudSession(
        FakeHass(),  # type: ignore[arg-type]
        cloud,
        APPLIANCE_ID,
        timeout=0.1,
        subscribe_delay=0,
    )
    rejected_upgrade = socket.InvalidStatus(SimpleNamespace(status_code=401))
    with patch.object(socket, "connect", AsyncMock(side_effect=rejected_upgrade)) as connect:
        with pytest.raises(socket.WeberCloudCredentialError, match="rejected"):
            await session.async_request_status()
    connect.assert_awaited_once()


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
async def test_recovery_duration_uses_first_of_repeated_failures() -> None:
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    statuses: list[dict[str, object]] = []
    errors: list[str] = []
    attempts = 0

    async def request_status() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError(f"offline {attempts}")
        session._connection = FakeConnection([routed(0x80)])  # type: ignore[assignment]
        return await session._async_receive_status()

    def publish_status(status: dict[str, object]) -> None:
        statuses.append(status)
        session._closed = True
        session.async_wake()

    def publish_error(message: str) -> None:
        errors.append(message)
        session.async_wake()

    monotonic = MagicMock(side_effect=[380.0, 389.0])
    with (
        patch.object(session, "async_request_status", AsyncMock(side_effect=request_status)),
        patch.object(socket, "time", SimpleNamespace(monotonic=monotonic)),
    ):
        task = asyncio.create_task(session.async_run(publish_status, publish_error))
        for _ in range(100):
            if statuses:
                break
            await asyncio.sleep(0)
        if not statuses:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            pytest.fail("recovery did not reach a fresh cook status")
        await task

    assert attempts == 3
    assert len(errors) == 2
    assert statuses[0]["kind"] == "cook_session_status"
    assert session.last_recovery_seconds == 9.0
    assert session.max_recovery_seconds == 9.0
    assert monotonic.call_count == 2


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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ConnectionResetError(), socket.ConnectionClosed(None, None)])
async def test_established_socket_recovers_once_without_failure_state(failure: Exception) -> None:
    first = FakeConnection([routed(0x80), failure])
    second = FakeConnection([routed(0x80)])
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, timeout=0.01, subscribe_delay=0
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])) as connect:
        await session.async_request_status()
        status = await session.async_request_status()
    assert status["kind"] == "cook_session_status"
    assert first.closed
    assert session.fast_recoveries == 1
    assert session.socket_connections == 2
    assert connect.await_count == 2


@pytest.mark.asyncio
async def test_fast_reconnect_failure_is_bounded_and_propagates() -> None:
    first = FakeConnection([routed(0x80), ConnectionResetError()])
    second = FakeConnection([ConnectionResetError("still disconnected")])
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, timeout=0.01, subscribe_delay=0
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])) as connect:
        await session.async_request_status()
        with pytest.raises(ConnectionResetError, match="still disconnected"):
            await session.async_request_status()
    assert session.fast_recoveries == 0
    assert connect.await_count == 2


@pytest.mark.asyncio
async def test_status_timeout_uses_at_most_one_fresh_connection_retry() -> None:
    first = FakeConnection([routed(0x80)])
    second = FakeConnection()
    session = socket.WeberCloudSession(
        FakeHass(),
        FakeCloudClient(),
        APPLIANCE_ID,
        timeout=0.001,
        request_timeout=0.1,
        subscribe_delay=0,
    )
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])) as connect:
        await session.async_request_status()
        with pytest.raises(TimeoutError):
            await session.async_request_status()
    assert first.closed
    assert connect.await_count == 2
    assert session.socket_connections == 2
    assert session.fast_recoveries == 0


@pytest.mark.asyncio
async def test_empty_timeout_error_has_actionable_diagnostics() -> None:
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    errors: list[str] = []
    with patch.object(session, "async_request_status", AsyncMock(side_effect=TimeoutError)):
        task = asyncio.create_task(session.async_run(MagicMock(), errors.append))
        for _ in range(20):
            if errors:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert session.last_error_type == "TimeoutError"
    assert "TimeoutError" in errors[0]
    assert "deadline" in errors[0]


@pytest.mark.asyncio
async def test_wake_network_failure_still_allows_websocket_status() -> None:
    cloud = FakeCloudClient()
    cloud.wake_messaging = MagicMock(side_effect=OSError("wake unavailable"))
    connection = FakeConnection([routed(0x80)])
    session = socket.WeberCloudSession(FakeHass(), cloud, APPLIANCE_ID, subscribe_delay=0)
    with patch.object(socket, "connect", AsyncMock(return_value=connection)):
        status = await session.async_request_status()
    assert status["kind"] == "cook_session_status"
    assert session.socket_connections == 1
    await session.async_close()


@pytest.mark.asyncio
async def test_upgrade_server_failure_preserves_original_exception() -> None:
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    error = socket.InvalidStatus(SimpleNamespace(status_code=503))
    with patch.object(socket, "connect", AsyncMock(side_effect=error)):
        with pytest.raises(socket.InvalidStatus) as raised:
            await session._async_connect()
    assert raised.value is error
    assert session._connection is None
    assert session.socket_connections == 0


@pytest.mark.asyncio
async def test_close_failure_still_clears_connection_and_subscription() -> None:
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    connection = FakeConnection()
    connection.close = AsyncMock(side_effect=OSError("already disconnected"))
    session._connection = connection
    session._subscribed = True
    await session.async_close()
    assert session._connection is None
    assert not session._subscribed
    assert session._closed


@pytest.mark.asyncio
async def test_subscription_pacing_and_unrecognized_frame_before_status() -> None:
    connection = FakeConnection([routed(0x82), routed(0x80)])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    with (
        patch.object(socket, "connect", AsyncMock(return_value=connection)),
        patch.object(socket.asyncio, "sleep", AsyncMock()) as sleep,
    ):
        status = await session.async_request_status()
    assert status["kind"] == "cook_session_status"
    assert session.received_types == [0x82, 0x80]
    assert sleep.await_count == 9
    assert all(call.args == (0.05,) for call in sleep.await_args_list)
    await session.async_close()


@pytest.mark.asyncio
async def test_run_cancellation_during_request_closes_session() -> None:
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    statuses, errors = MagicMock(), MagicMock()
    with patch.object(
        session, "async_request_status", AsyncMock(side_effect=asyncio.CancelledError)
    ):
        with pytest.raises(asyncio.CancelledError):
            await session.async_run(statuses, errors)
    statuses.assert_not_called()
    errors.assert_not_called()
    assert session._closed


@pytest.mark.asyncio
async def test_run_delay_timeout_retries_then_honors_close() -> None:
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    statuses = []

    def publish(status: dict[str, object]) -> None:
        statuses.append(status)
        if len(statuses) == 2:
            session._closed = True
            session.async_wake()

    with (
        patch.object(socket, "STATUS_INTERVAL", 0),
        patch.object(session, "async_request_status", AsyncMock(return_value={"ok": True})) as poll,
    ):
        await session.async_run(publish, MagicMock())
    assert statuses == [{"ok": True}, {"ok": True}]
    assert poll.await_count == 2
    assert session._closed

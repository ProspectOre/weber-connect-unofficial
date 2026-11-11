"""Program reads must remain optional, narrowly scoped, and read-only."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.weber_connect import weber_cloud_socket as socket
from tests_native.test_cloud_socket import (
    APPLIANCE_ID,
    DEVICE_ID,
    FakeCloudClient,
    FakeConnection,
    FakeHass,
    tlv,
)
from tests_native.test_probe_targets import PROGRAM_ID, program_frame


def message(kind, payload=b"", sequence=7, version=10):
    return socket.encode_routed_message(
        APPLIANCE_ID, DEVICE_ID, sequence, kind, payload, message_version=version
    )


def active_probe(step=0, state=5):
    # SESSION_STATES: ACTIVE=5. This is a fixture, not a live-device capture.
    return tlv(
        4,
        b"".join(
            [
                tlv(1, b"\x02"),
                tlv(2, b"\x07"),
                tlv(3, PROGRAM_ID),
                tlv(4, b"\x00"),
                tlv(7, bytes([step])),
                tlv(12, bytes([state])),
            ]
        ),
    )


@pytest.mark.parametrize("version", [5, 10, 11])
async def test_probe_read_has_exact_selector_and_supported_message_version(version):
    connection = FakeConnection()
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    sequence = await session._async_send(0x0B, b"\x01\x0f", message_version=version)
    sent = socket.decode_routed_message(connection.sent[0])
    assert (sent.type_value, sent.payload, sent.message_version, sent.sequence) == (
        0x0B,
        b"\x01\x0f",
        version,
        sequence,
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x01",
        b"\x01\x00\x00",
        b"\x00\x00",
        b"\x02\x00",
        b"\x03\x00",
        b"\x01\x10",
        bytearray(b"\x01\x00"),
    ],
)
async def test_program_selector_validation_happens_before_connection(payload):
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    with patch.object(session, "_async_connect", AsyncMock()) as connect:
        with pytest.raises(socket.WeberCloudSocketError, match="telemetry requests"):
            await session._async_send(0x0B, payload)
    connect.assert_not_awaited()
    assert session._sequence == 1


@pytest.mark.parametrize("version", [True, 4, 12, "10"])
async def test_unknown_program_version_never_reaches_wire(version):
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    with patch.object(session, "_async_connect", AsyncMock()) as connect:
        with pytest.raises(socket.WeberCloudSocketError):
            await session._async_send(0x0B, b"\x01\x00", message_version=version)
    connect.assert_not_awaited()


async def test_live_status_fetches_program_and_advances_target_without_reconnecting():
    connection = FakeConnection([message(0x80, active_probe())])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    first = await session._async_receive_status()
    assert first["probes"][0]["target_temperature_c"] is None
    assert len(connection.sent) == 1
    request = socket.decode_routed_message(connection.sent[0])
    assert (request.type_value, request.payload) == (0x0B, b"\x01\x02")
    connection.responses.extend(
        [
            message(0x86, program_frame(), sequence=869),
            message(0x80, active_probe()),
            message(0x80, active_probe(step=1)),
            message(0x80, active_probe(state=0)),
        ]
    )
    second = await session._async_receive_status()
    assert second["probes"][0]["target_temperature_c"] == 50
    third = await session._async_receive_status()
    assert third["probes"][0]["target_temperature_c"] is None
    assert third["probes"][0]["target_lower_temperature_c"] == 40
    assert third["probes"][0]["target_upper_temperature_c"] == 60
    fourth = await session._async_receive_status()
    assert fourth["probes"][0]["target_upper_temperature_c"] is None
    assert len(connection.sent) == 1
    assert not connection.closed


async def test_ambiguous_error_surfaces_and_suspends_optional_reads_during_recovery():
    connection = FakeConnection([message(0x80, active_probe())])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    assert not session.program_reads_suspended
    await session._async_receive_status()
    connection.responses.append(message(0x87, sequence=869))
    with pytest.raises(socket.WeberCloudSocketError, match="rejected"):
        await session._async_receive_status()
    assert session.program_reads_suspended
    await session._async_close_connection()
    replacement = FakeConnection([message(0x80, active_probe()), message(0x87, sequence=900)])
    session._connection = replacement
    status = await session._async_receive_status()
    assert status["probes"][0]["target_temperature_c"] is None
    assert not replacement.sent
    with pytest.raises(socket.WeberCloudSocketError, match="rejected"):
        await session._async_receive_status()


async def test_socket_rotation_discards_program_cache_and_request_correlation():
    connection = FakeConnection([message(0x80, active_probe())])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    await session._async_receive_status()
    await session._async_close_connection()
    replacement = FakeConnection(
        [message(0x86, program_frame(), sequence=1), message(0x80, active_probe())]
    )
    session._connection = replacement
    result = await session._async_receive_status()
    assert result["probes"][0]["target_temperature_c"] is None
    assert len(replacement.sent) == 1


async def test_observed_manual_target_response_has_independent_controller_sequence():
    # Sanitized OEM alert: no credentials, device UUIDs, serials or user text.
    payload = bytes.fromhex(
        "01020000000000000000000000000000000000095072696d6974697665"
        "00010200000000008001e801000003020000"
    )
    status = active_probe(step=2, state=9).replace(PROGRAM_ID, bytes(16))
    connection = FakeConnection([message(0x80, status)])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    await session._async_receive_status()
    request = socket.decode_routed_message(connection.sent[0])
    assert request.sequence != 869
    connection.responses.extend([message(0x86, payload, sequence=869), message(0x80, status)])
    result = await session._async_receive_status()
    assert result["probes"][0]["target_temperature_c"] == 48.8


async def test_run_consumes_program_and_session_changes_between_polls():
    connection = FakeConnection(
        [
            message(0x80, active_probe()),
            message(0x86, program_frame(), sequence=869),
            message(0x80, active_probe()),
            message(0x80, active_probe(step=1)),
            message(0x80, active_probe(state=0)),
        ]
    )
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    session._subscribed = True
    statuses, errors = [], []

    def publish(status):
        statuses.append(status["probes"][0])
        if len(statuses) == 4:
            session._closed = True

    # All queued transitions must arrive well before the next ten-second poll.
    await asyncio.wait_for(session.async_run(publish, errors.append), timeout=1)
    assert not errors
    assert [row["target_temperature_c"] for row in statuses] == [None, 50, None, None]
    assert statuses[2]["target_lower_temperature_c"] == 40
    assert statuses[3]["target_lower_temperature_c"] is None
    assert [socket.decode_routed_message(raw).type_value for raw in connection.sent] == [
        0x07,
        0x05,
        0x0B,
    ]


async def test_program_error_between_polls_keeps_existing_recovery_behavior():
    connection = FakeConnection([message(0x80, active_probe()), message(0x87)])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    session._subscribed = True
    errors = []

    def error(message):
        errors.append(message)
        session._closed = True
        session.async_wake()

    await asyncio.wait_for(session.async_run(lambda _: None, error), timeout=1)
    assert len(errors) == 1
    assert "rejected" in errors[0]
    assert session.program_reads_suspended
    assert connection.closed


@pytest.mark.parametrize("wake", [False, True])
async def test_between_poll_timeout_or_wake_awaits_reader_cancellation(wake):
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def receive():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    with patch.object(session, "_async_receive_status", receive):
        listener = asyncio.create_task(session._async_listen_until_poll(0.05, lambda _: None))
        await started.wait()
        if wake:
            session.async_wake()
        await listener
    assert cancelled.is_set()


@pytest.mark.parametrize("stop", ["closed", "wake", "deadline"])
async def test_between_poll_listener_does_not_start_after_stop(stop):
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    if stop == "closed":
        session._closed = True
    elif stop == "wake":
        session.async_wake()
    with patch.object(session, "_async_receive_status", AsyncMock()) as receive:
        await session._async_listen_until_poll(0 if stop == "deadline" else 10, lambda _: None)
    receive.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [socket.ConnectionClosed(None, None), OSError(), TimeoutError()]
)
async def test_between_poll_transport_failure_has_one_fast_recovery(failure):
    connection = FakeConnection([message(0x80, active_probe()), failure])
    replacement = FakeConnection([message(0x80, active_probe())])
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, subscribe_delay=0
    )
    session._connection = connection
    session._subscribed = True
    statuses, errors = [], []

    def publish(status):
        statuses.append(status)
        if len(statuses) == 2:
            session._closed = True

    with patch.object(socket, "connect", AsyncMock(return_value=replacement)) as connect:
        await asyncio.wait_for(session.async_run(publish, errors.append), timeout=2)
    connect.assert_awaited_once()
    assert not errors
    assert session.fast_recoveries == 1
    assert connection.closed and replacement.closed


async def test_between_poll_failed_fast_recovery_reports_failure():
    connection = FakeConnection([message(0x80, active_probe()), OSError("closed")])
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)
    session._connection = connection
    session._subscribed = True
    errors = []

    def error(message):
        errors.append(message)
        session._closed = True
        session.async_wake()

    with patch.object(socket, "connect", AsyncMock(side_effect=OSError("retry failed"))):
        await asyncio.wait_for(session.async_run(lambda _: None, error), timeout=2)
    assert len(errors) == 1
    assert "retry failed" in errors[0]
    assert session.fast_recoveries == 0


async def test_between_poll_repeated_failure_uses_normal_error_path():
    connection = FakeConnection([message(0x80, active_probe()), OSError("first close")])
    replacement = FakeConnection([message(0x80, active_probe()), OSError("second close")])
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, subscribe_delay=0
    )
    session._connection = connection
    session._subscribed = True
    errors = []

    def error(message):
        errors.append(message)
        session._closed = True
        session.async_wake()

    with patch.object(socket, "connect", AsyncMock(return_value=replacement)) as connect:
        await asyncio.wait_for(session.async_run(lambda _: None, error), timeout=2)
    connect.assert_awaited_once()
    assert len(errors) == 1
    assert "second close" in errors[0]
    assert session.fast_recoveries == 1
    assert connection.closed and replacement.closed


@pytest.mark.parametrize("stop", ["closed", "wake"])
async def test_between_poll_failure_during_stop_does_not_reconnect(stop):
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)

    async def receive():
        if stop == "closed":
            session._closed = True
        else:
            session.async_wake()
        raise OSError("closed during stop")

    with (
        patch.object(session, "_async_receive_status", receive),
        patch.object(session, "_async_recover_status", AsyncMock()) as recover,
    ):
        await session._async_listen_until_poll(1, lambda _: None)
    recover.assert_not_awaited()


async def test_slow_poll_retains_one_second_floor():
    session = socket.WeberCloudSession(FakeHass(), FakeCloudClient(), APPLIANCE_ID)

    def publish(_):
        session._closed = True

    with (
        patch.object(socket, "STATUS_INTERVAL", 0),
        patch.object(session, "async_request_status", AsyncMock(return_value={})),
        patch.object(session, "_async_listen_until_poll", AsyncMock()) as listen,
    ):
        await session.async_run(publish, lambda _: None)
    listen.assert_awaited_once_with(1.0, publish)

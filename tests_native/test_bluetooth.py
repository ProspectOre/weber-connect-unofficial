"""Protocol-level tests for Home Assistant Bluetooth and proxy connections."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, call, patch

import pytest
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from bleak_retry_connector import BleakOutOfConnectionSlotsError

from custom_components.weber_connect import bluetooth as transport
from custom_components.weber_connect.models import CompanionIdentity
from custom_components.weber_connect.saber_frames import build_command_frame, crc8

ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTITY = CompanionIdentity("11" * 16, "33" * 64)


@pytest.fixture(autouse=True)
def clear_advertisement_history() -> object:
    """Isolate Home Assistant's Bluetooth manager and expose cache clearing."""

    with patch.object(transport.bluetooth, "async_clear_advertisement_history") as clear:
        yield clear


def _pairing_required() -> bytes:
    return build_command_frame(1, 10, 0xF1, b"")


def _pairing_confirmed() -> bytes:
    payload = bytes(range(16)) + bytes(range(64)) + b"\x00"
    return build_command_frame(2, 10, 0x85, payload)


def _status() -> bytes:
    return build_command_frame(4, 10, 0x80, b"")


def _appliance_status() -> bytes:
    payload = bytes([1, 1, 64, 2, 1, 1])
    return build_command_frame(3, 10, 0x83, payload)


def test_payload_rejects_bad_length_crc_tail_and_extra_bytes() -> None:
    valid = _status()
    assert transport._payload(valid)[0] == 0x80

    bad_length = bytearray(valid)
    bad_length[4:6] = (len(valid)).to_bytes(2, "little")
    with pytest.raises(transport.WeberBluetoothError, match="transport"):
        transport._payload(bytes(bad_length))

    bad_crc = bytearray(valid)
    bad_crc[-2] ^= 0xFF
    with pytest.raises(transport.WeberBluetoothError, match="corrupted"):
        transport._payload(bytes(bad_crc))

    bad_tail = bytearray(valid)
    bad_tail[-1] = 0
    with pytest.raises(transport.WeberBluetoothError, match="corrupted"):
        transport._payload(bytes(bad_tail))

    with pytest.raises(transport.WeberBluetoothError, match="transport"):
        transport._payload(valid + b"extra")

    envelope_extra = bytearray(valid)
    envelope_extra[4:6] = (int.from_bytes(valid[4:6], "little") + 1).to_bytes(2, "little")
    envelope_extra += b"extra"[:1]
    with pytest.raises(transport.WeberBluetoothError, match="corrupted"):
        transport._payload(bytes(envelope_extra))

    encrypted = bytearray(valid)
    encrypted[7] = 1
    encrypted[-2] = crc8(bytes(encrypted[7:-2]))
    with pytest.raises(transport.WeberBluetoothError, match="encrypted"):
        transport._payload(bytes(encrypted))


def test_pairing_path_rejects_unauthenticated_status_telemetry() -> None:
    with pytest.raises(transport.WeberBluetoothError, match="unauthenticated"):
        transport._pairing_payload(_status())
    with pytest.raises(transport.WeberBluetoothError, match="unauthenticated"):
        transport._pairing_payload(_appliance_status())


class FakeClient:
    """Small connected GATT client with scripted response reads."""

    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.responses = list(responses or [])
        self.callbacks: dict[str, object] = {}
        self.writes: list[tuple[str, bytes, bool]] = []
        self.disconnected = False
        self.is_connected = True

    async def start_notify(self, uuid: str, callback: object) -> None:
        self.callbacks[uuid] = callback

    async def stop_notify(self, uuid: str) -> None:
        self.callbacks.pop(uuid, None)

    async def read_gatt_char(self, uuid: str) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = True) -> None:
        self.writes.append((uuid, bytes(data), response))

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


@pytest.mark.asyncio
async def test_pairing_confirms_and_releases_proxy_connection(
    clear_advertisement_history: object,
) -> None:
    client = FakeClient([_pairing_required(), _pairing_confirmed()])
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        result = await transport.async_pair(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY,
            confirmation_timeout=0.5,
        )
    assert result.message_version == 11
    assert result.appliance_id == bytes(range(16)).hex()
    assert client.disconnected
    assert any(uuid == transport.COMMAND_UUID for uuid, _data, _response in client.writes)
    clear_advertisement_history.assert_called_once_with(  # type: ignore[attr-defined]
        ANY,
        ADDRESS,
    )


@pytest.mark.asyncio
async def test_pairing_ignores_status_frames_without_publishing_them() -> None:
    client = FakeClient([_status(), _pairing_required(), _pairing_confirmed()])
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        result = await transport.async_pair(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY,
            confirmation_timeout=0.5,
        )

    assert result.appliance_id == bytes(range(16)).hex()
    assert client.disconnected is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_pairing_releases_connection_when_setup_is_interrupted(
    clear_advertisement_history: object,
    failure: BaseException,
) -> None:
    client = FakeClient()
    client.write_gatt_char = AsyncMock(side_effect=failure)
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(type(failure)):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)

    assert client.disconnected is True
    clear_advertisement_history.assert_called_once_with(  # type: ignore[attr-defined]
        ANY,
        ADDRESS,
    )


@pytest.mark.asyncio
async def test_connect_re_resolves_best_adapter_or_proxy_for_retries() -> None:
    first_device = SimpleNamespace(address=ADDRESS, name="Hub")
    second_device = SimpleNamespace(address=ADDRESS, name="Hub via proxy")
    client = FakeClient()
    resolver = AsyncMock()
    establish = AsyncMock(return_value=client)
    with (
        patch.object(
            transport.bluetooth,
            "async_ble_device_from_address",
            side_effect=[first_device, second_device],
        ) as resolve,
        patch.object(transport, "establish_connection", establish),
    ):
        assert await transport._connect(SimpleNamespace(), ADDRESS) is client
        callback = establish.await_args.kwargs["ble_device_callback"]
        assert callback() is second_device
    assert resolve.call_count == 2
    resolver.assert_not_awaited()
    assert establish.await_args.kwargs["use_services_cache"] is True
    assert establish.await_args.kwargs["max_attempts"] == 1
    assert establish.await_args.kwargs["timeout"] == transport.CONNECTION_TIMEOUT


@pytest.mark.asyncio
async def test_pairing_allows_additional_connection_attempts() -> None:
    hass = SimpleNamespace()
    identity = transport.generate_identity()
    with patch.object(
        transport,
        "_connect",
        AsyncMock(side_effect=transport.WeberBluetoothError("not reachable")),
    ) as connect:
        with pytest.raises(transport.WeberBluetoothError, match="not reachable"):
            await transport.async_pair(hass, ADDRESS, identity)

    connect.assert_awaited_once_with(
        hass,
        ADDRESS,
        max_attempts=3,
        use_services_cache=False,
    )


@pytest.mark.asyncio
async def test_pairing_reconnects_when_restarted_hub_services_are_incomplete(
    clear_advertisement_history: object,
) -> None:
    stale_client = FakeClient()
    stale_client.start_notify = AsyncMock(
        side_effect=BleakCharacteristicNotFoundError(transport.RESPONSE_UUID)
    )
    fresh_client = FakeClient([_pairing_required(), _pairing_confirmed(), _pairing_required()])

    with (
        patch.object(
            transport,
            "_connect",
            AsyncMock(side_effect=[stale_client, fresh_client]),
        ) as connect,
        patch.object(transport.asyncio, "sleep", AsyncMock()) as sleep,
    ):
        result = await transport.async_pair(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY,
            confirmation_timeout=0.5,
        )

    assert result.appliance_id == bytes(range(16)).hex()
    assert connect.await_count == 2
    assert stale_client.disconnected is True
    sleep.assert_awaited_once_with(1.0)
    clear_advertisement_history.assert_any_call(ANY, ADDRESS)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pairing_explains_services_that_never_become_ready() -> None:
    clients = [FakeClient() for _ in range(3)]
    for client in clients:
        client.start_notify = AsyncMock(
            side_effect=BleakCharacteristicNotFoundError(transport.RESPONSE_UUID)
        )

    with (
        patch.object(transport, "_connect", AsyncMock(side_effect=clients)) as connect,
        patch.object(transport.asyncio, "sleep", AsyncMock()) as sleep,
    ):
        with pytest.raises(transport.WeberBluetoothError, match="services were not ready") as error:
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)

    assert all(client.disconnected for client in clients)
    assert connect.await_count == 3
    assert sleep.await_args_list == [call(1.0), call(2.0)]
    assert error.value.__cause__ is clients[-1].start_notify.side_effect


@pytest.mark.asyncio
async def test_connect_normalizes_busy_proxy_slots() -> None:
    device = SimpleNamespace(address=ADDRESS, name="Hub via proxy")
    with (
        patch.object(
            transport.bluetooth,
            "async_ble_device_from_address",
            return_value=device,
        ),
        patch.object(
            transport,
            "establish_connection",
            AsyncMock(side_effect=BleakOutOfConnectionSlotsError(ADDRESS)),
        ),
    ):
        with pytest.raises(transport.WeberBluetoothError, match="slot"):
            await transport._connect(SimpleNamespace(), ADDRESS)


@pytest.mark.asyncio
async def test_connect_explains_why_no_proxy_can_reach_the_hub() -> None:
    with (
        patch.object(
            transport.bluetooth,
            "async_ble_device_from_address",
            return_value=None,
        ),
        patch.object(
            transport.bluetooth,
            "async_address_reachability_diagnostics",
            return_value="The active proxy last saw it five minutes ago.",
        ) as diagnostics,
    ):
        with pytest.raises(transport.WeberBluetoothError, match="five minutes ago"):
            await transport._connect(SimpleNamespace(), ADDRESS)

    diagnostics.assert_called_once_with(
        ANY,
        ADDRESS,
        transport.bluetooth.BluetoothReachabilityIntent.CONNECTION,
    )


@pytest.mark.asyncio
async def test_connect_normalizes_transport_errors_and_safe_disconnect() -> None:
    device = SimpleNamespace(address=ADDRESS, name="Hub")
    with (
        patch.object(
            transport.bluetooth,
            "async_ble_device_from_address",
            return_value=device,
        ),
        patch.object(
            transport,
            "establish_connection",
            AsyncMock(side_effect=BleakError("radio unavailable")),
        ),
    ):
        with pytest.raises(transport.WeberBluetoothError, match="could not be established"):
            await transport._connect(SimpleNamespace(), ADDRESS)

    client = FakeClient()
    client.disconnect = AsyncMock(side_effect=RuntimeError("already gone"))
    await transport._safe_disconnect(client)


@pytest.fixture
def pairing_clock():
    """Advance protocol deadlines without waiting on wall-clock Bluetooth timeouts."""
    clock = SimpleNamespace(now=0.0)

    async def advance(delay):
        clock.now += delay

    with (
        patch.object(
            transport.asyncio,
            "get_running_loop",
            return_value=SimpleNamespace(time=lambda: clock.now),
        ),
        patch.object(transport.asyncio, "sleep", side_effect=advance),
    ):
        yield clock


async def test_pairing_polls_when_notifications_are_unavailable(pairing_clock):
    client = FakeClient([_pairing_required(), _pairing_confirmed()])
    client.start_notify = AsyncMock(side_effect=BleakError("notifications unavailable"))
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        result = await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert result.appliance_id == bytes(range(16)).hex()
    assert client.disconnected


async def test_pairing_notification_queue_and_ignored_confirmation_telemetry(pairing_clock):
    client = FakeClient()
    original_write = client.write_gatt_char
    commands = 0

    async def write(uuid, data, response=True):
        nonlocal commands
        await original_write(uuid, data, response)
        if uuid != transport.COMMAND_UUID:
            return
        commands += 1
        callback = client.callbacks[transport.RESPONSE_UUID]
        if commands == 1:
            callback(None, bytearray(_pairing_required()))
        elif commands == 2:
            for frame in (_status(), _pairing_required(), _pairing_confirmed()):
                callback(None, bytearray(frame))

    client.write_gatt_char = write
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        result = await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert result.appliance_id == bytes(range(16)).hex()
    assert client.disconnected


async def test_pairing_retries_silent_handshake_and_times_out_confirmation(pairing_clock):
    client = FakeClient()
    client.read_gatt_char = AsyncMock(side_effect=BleakError("read unavailable"))
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(transport.WeberBluetoothError, match="did not confirm"):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY, confirmation_timeout=3)
    assert len([write for write in client.writes if write[0] == transport.COMMAND_UUID]) == 4
    assert client.disconnected


async def test_pairing_does_not_reuse_stale_polled_response(pairing_clock):
    client = FakeClient()
    client.read_gatt_char = AsyncMock(return_value=_pairing_required())
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(transport.WeberBluetoothError, match="did not confirm"):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY, confirmation_timeout=3)
    assert client.disconnected


@pytest.mark.parametrize("status", [1, 255])
async def test_pairing_reports_rejected_and_unknown_status(pairing_clock, status):
    rejected = build_command_frame(
        2, 10, 0x85, bytes(range(16)) + bytes(range(64)) + bytes([status])
    )
    client = FakeClient([_pairing_required(), rejected])
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(transport.WeberBluetoothError, match="for pairing"):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert client.disconnected


async def test_pairing_uses_negotiated_version_for_later_commands(pairing_clock):
    error = build_command_frame(1, 10, 0x87, b"\x00\x01\x00")
    client = FakeClient([error, _pairing_required(), _pairing_confirmed()])
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        result = await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert result.message_version == 10
    frames = [
        transport._decoded(data)
        for uuid, data, _ in client.writes
        if uuid == transport.COMMAND_UUID
    ]
    assert [frame["envelope"]["body_plain_candidate"]["message_version"] for frame in frames] == [
        11,
        10,
        10,
        10,
    ]


async def test_pairing_continues_after_unrecognized_handshake_responses(pairing_clock):
    client = FakeClient(
        [
            build_command_frame(1, 10, 0x85, b""),
            build_command_frame(2, 10, 0x87, b"\x00\x01\xff"),
            _pairing_confirmed(),
            _pairing_confirmed(),
        ]
    )
    # A notification may repeat a valid response; polled values are deduplicated.
    original_write = client.write_gatt_char

    async def write(uuid, data, response=True):
        await original_write(uuid, data, response)
        if len(client.writes) == 5:
            client.callbacks[transport.RESPONSE_UUID](None, bytearray(_pairing_confirmed()))

    client.write_gatt_char = write
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        result = await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert result.message_version == 11


async def test_pairing_translates_services_disappearing_after_setup(pairing_clock):
    client = FakeClient()
    client.write_gatt_char = AsyncMock(
        side_effect=[None, BleakCharacteristicNotFoundError(transport.COMMAND_UUID)]
    )
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(transport.WeberBluetoothError, match="services changed"):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert client.disconnected


async def test_pairing_rejects_invalid_decoded_identity(pairing_clock):
    client = FakeClient([_pairing_required(), _pairing_confirmed()])
    with (
        patch.object(transport, "_connect", AsyncMock(return_value=client)),
        patch.object(
            transport,
            "_pairing_payload",
            side_effect=[
                (0xF1, None),
                (0x85, {"kind": "pairing_response", "status": "CONFIRMED", "appliance_id": "bad"}),
            ],
        ),
    ):
        with pytest.raises(transport.WeberBluetoothError, match="invalid appliance identity"):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert client.disconnected


async def test_pairing_keeps_version_when_decoder_has_no_integer_version(pairing_clock):
    client = FakeClient([_pairing_required(), _pairing_confirmed()])
    with (
        patch.object(transport, "_connect", AsyncMock(return_value=client)),
        patch.object(
            transport,
            "_pairing_payload",
            side_effect=[
                (0x87, {"kind": "error", "error_type": "UNSUPPORTED_MESSAGE_VERSION"}),
                (0xF1, None),
                (
                    0x85,
                    {"kind": "pairing_response", "status": "CONFIRMED", "appliance_id": "11" * 16},
                ),
            ],
        ),
        patch.object(
            transport,
            "_decoded",
            return_value={"envelope": {"body_plain_candidate": {"message_version": None}}},
        ),
    ):
        # Each stage needs a fresh transport frame to pass polling deduplication.
        client.responses = [b"error", b"required", b"confirmed"]
        result = await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)
    assert result.message_version == 11

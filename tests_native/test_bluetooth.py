"""Protocol-level tests for Home Assistant Bluetooth and proxy connections."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

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
        type_value = transport._payload(bytes(data))[0] if len(data) > 1 else None
        if uuid == transport.SESSION_UUID or (
            uuid == transport.COMMAND_UUID and type_value == 0x05
        ):
            callback = self.callbacks.get(transport.STATUS_UUID)
            if callback is not None:
                callback(transport.STATUS_UUID, bytearray(_status()))  # type: ignore[operator]

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
        patch.object(transport, "_connect", AsyncMock(side_effect=clients)),
        patch.object(transport.asyncio, "sleep", AsyncMock()),
    ):
        with pytest.raises(transport.WeberBluetoothError, match="services were not ready"):
            await transport.async_pair(SimpleNamespace(), ADDRESS, IDENTITY)

    assert all(client.disconnected for client in clients)


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
async def test_persistent_session_reuses_one_proxy_connection() -> None:
    client = FakeClient()
    with patch.object(transport, "_connect", AsyncMock(return_value=client)) as connect:
        session = transport.WeberBluetoothSession(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY.companion_id,
            10,
        )

        first = await session.async_read_status(timeout=0.5)
        second = await session.async_read_status(timeout=0.5)

        assert first["kind"] == "cook_session_status"
        assert second["kind"] == "cook_session_status"
        assert [uuid for uuid, _data, _response in client.writes] == [
            transport.SESSION_UUID,
            transport.COMMAND_UUID,
        ]
        assert [transport._payload(data)[0] for _uuid, data, _response in client.writes] == [
            0x70,
            0x05,
        ]
        connect.assert_awaited_once_with(
            session.hass,
            ADDRESS,
            max_attempts=transport.SESSION_CONNECT_ATTEMPTS,
            use_services_cache=True,
            disconnected_callback=ANY,
        )
        assert client.disconnected is False

        await session.async_close()

    assert client.disconnected is True


@pytest.mark.asyncio
async def test_persistent_session_reconnects_after_link_loss() -> None:
    first_client = FakeClient()
    second_client = FakeClient()
    with patch.object(
        transport,
        "_connect",
        AsyncMock(side_effect=[first_client, second_client]),
    ) as connect:
        session = transport.WeberBluetoothSession(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY.companion_id,
            10,
        )
        await session.async_read_status(timeout=0.5)
        first_client.is_connected = False

        status = await session.async_read_status(timeout=0.5)

    assert status["kind"] == "cook_session_status"
    assert first_client.disconnected is True
    assert connect.await_args_list[1].kwargs == {
        "max_attempts": transport.SESSION_CONNECT_ATTEMPTS,
        "use_services_cache": True,
        "disconnected_callback": ANY,
    }


@pytest.mark.asyncio
async def test_persistent_session_prefers_cached_services_on_first_connect() -> None:
    client = FakeClient()
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)

    with patch.object(transport, "_connect", AsyncMock(return_value=client)) as connect:
        status = await session.async_read_status(timeout=0.5)

    assert status["kind"] == "cook_session_status"
    assert connect.await_args.kwargs == {
        "max_attempts": transport.SESSION_CONNECT_ATTEMPTS,
        "use_services_cache": True,
        "disconnected_callback": ANY,
    }


@pytest.mark.asyncio
async def test_persistent_session_refreshes_stale_proxy_services() -> None:
    stale_client = FakeClient()
    stale_client.start_notify = AsyncMock(
        side_effect=BleakCharacteristicNotFoundError(transport.STATUS_UUID)
    )
    fresh_client = FakeClient()
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)

    with patch.object(
        transport,
        "_connect",
        AsyncMock(side_effect=[stale_client, fresh_client]),
    ) as connect:
        status = await session.async_read_status(timeout=0.5)

    assert status["kind"] == "cook_session_status"
    assert stale_client.disconnected is True
    assert connect.await_args_list[0].kwargs["use_services_cache"] is True
    assert connect.await_args_list[1].kwargs["use_services_cache"] is False


@pytest.mark.asyncio
async def test_persistent_session_explains_missing_fresh_services() -> None:
    client = FakeClient()
    client.start_notify = AsyncMock(
        side_effect=BleakCharacteristicNotFoundError(transport.STATUS_UUID)
    )
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)

    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(transport.WeberBluetoothError, match="could not be discovered"):
            await session.async_read_status(timeout=0.5)

    assert client.disconnected is True


@pytest.mark.asyncio
async def test_persistent_session_tolerates_optional_notification_failure() -> None:
    client = FakeClient()
    original_start_notify = client.start_notify

    async def start_notify(uuid: str, callback: object) -> None:
        if uuid == transport.NOTIFICATION_UUID:
            raise RuntimeError("not supported")
        await original_start_notify(uuid, callback)

    client.start_notify = start_notify  # type: ignore[method-assign]
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)

    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        status = await session.async_read_status(timeout=0.5)

    assert status["kind"] == "cook_session_status"


@pytest.mark.asyncio
async def test_persistent_session_requires_one_status_notification() -> None:
    client = FakeClient()
    client.start_notify = AsyncMock(side_effect=RuntimeError("not supported"))
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)

    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        with pytest.raises(transport.WeberBluetoothError, match="usable status notification"):
            await session.async_read_status(timeout=0.5)

    assert client.disconnected is True


@pytest.mark.asyncio
async def test_persistent_session_normalizes_timeout_and_interruption() -> None:
    timeout_client = FakeClient()
    timeout_client.write_gatt_char = AsyncMock()
    interrupted_client = FakeClient()
    interrupted_client.write_gatt_char = AsyncMock(side_effect=BleakError("link lost"))
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)

    with patch.object(
        transport,
        "_connect",
        AsyncMock(side_effect=[timeout_client, interrupted_client]),
    ):
        with pytest.raises(transport.WeberBluetoothError, match="fresh probe reading"):
            await session.async_read_status(timeout=0.001)
        timeout_client.is_connected = False
        with pytest.raises(transport.WeberBluetoothError, match="interrupted"):
            await session.async_read_status(timeout=0.5)

    assert timeout_client.disconnected is True
    assert interrupted_client.disconnected is True


@pytest.mark.asyncio
async def test_persistent_session_callbacks_and_disconnect_wake() -> None:
    statuses: list[dict[str, object]] = []
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)
    session._status_callback = statuses.append

    session._handle_status(transport.STATUS_UUID, bytearray(_status()))
    assert statuses[0]["kind"] == "cook_session_status"
    assert session._received.is_set()

    session._received.clear()
    corrupted = bytearray(_status())
    corrupted[-2] ^= 0xFF
    session._handle_status(transport.STATUS_UUID, corrupted)
    assert not session._received.is_set()

    session._wake.clear()
    session._handle_disconnect(FakeClient())
    assert session._wake.is_set()


@pytest.mark.asyncio
async def test_persistent_session_run_retries_and_closes() -> None:
    session = transport.WeberBluetoothSession(SimpleNamespace(), ADDRESS, IDENTITY.companion_id, 10)
    errors: list[str] = []

    async def fail_once() -> dict[str, object]:
        session._closed = True
        session.async_wake()
        raise transport.WeberBluetoothError("sleeping")

    session.async_read_status = fail_once  # type: ignore[method-assign]
    await session.async_run(lambda _status: None, errors.append)

    assert errors == ["sleeping"]
    assert session._status_callback is None
    assert session._closed is True


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

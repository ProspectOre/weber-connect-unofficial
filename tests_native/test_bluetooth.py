"""Protocol-level tests for Home Assistant Bluetooth and proxy connections."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from bleak.exc import BleakCharacteristicNotFoundError
from bleak_retry_connector import BleakOutOfConnectionSlotsError

from custom_components.weber_connect import bluetooth as transport
from custom_components.weber_connect.models import CompanionIdentity
from custom_components.weber_connect.saber_frames import build_command_frame

ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTITY = CompanionIdentity("11" * 16, "22" * 64, "33" * 64)


def _pairing_required() -> bytes:
    return build_command_frame(1, 10, 0xF1, b"")


def _pairing_confirmed() -> bytes:
    payload = bytes(range(16)) + bytes(range(64)) + b"\x00"
    return build_command_frame(2, 10, 0x85, payload)


def _status() -> bytes:
    return build_command_frame(4, 10, 0x80, b"")


class FakeClient:
    """Small connected GATT client with scripted response reads."""

    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.responses = list(responses or [])
        self.callbacks: dict[str, object] = {}
        self.writes: list[tuple[str, bytes, bool]] = []
        self.disconnected = False

    async def start_notify(self, uuid: str, callback: object) -> None:
        self.callbacks[uuid] = callback

    async def stop_notify(self, uuid: str) -> None:
        self.callbacks.pop(uuid, None)

    async def read_gatt_char(self, uuid: str) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = True) -> None:
        self.writes.append((uuid, bytes(data), response))
        if uuid == transport.SESSION_UUID and len(data) > 1:
            callback = self.callbacks.get(transport.STATUS_UUID)
            if callback is not None:
                callback(transport.STATUS_UUID, bytearray(_status()))  # type: ignore[operator]

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.asyncio
async def test_pairing_confirms_and_releases_proxy_connection() -> None:
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
    assert len(result.appliance_public_key.replace(":", "")) == 128
    assert client.disconnected
    assert any(uuid == transport.COMMAND_UUID for uuid, _data, _response in client.writes)


@pytest.mark.asyncio
async def test_status_read_decodes_and_releases_proxy_connection() -> None:
    client = FakeClient()
    with patch.object(transport, "_connect", AsyncMock(return_value=client)):
        status = await transport.async_read_status(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY.companion_id,
            10,
            timeout=0.5,
        )
    assert status["kind"] == "cook_session_status"
    assert status["probe_count"] == 0
    assert client.disconnected


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
    assert establish.await_args.kwargs["timeout"] == 10.0


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
async def test_status_read_refreshes_a_stale_services_cache() -> None:
    stale_client = FakeClient()
    stale_client.write_gatt_char = AsyncMock(
        side_effect=BleakCharacteristicNotFoundError(transport.SESSION_UUID)
    )
    fresh_client = FakeClient()

    with patch.object(
        transport,
        "_connect",
        AsyncMock(side_effect=[stale_client, fresh_client]),
    ) as connect:
        status = await transport.async_read_status(
            SimpleNamespace(),
            ADDRESS,
            IDENTITY.companion_id,
            10,
            timeout=0.5,
        )

    assert status["kind"] == "cook_session_status"
    assert stale_client.disconnected
    assert fresh_client.disconnected
    assert connect.await_args_list[0].kwargs == {"use_services_cache": True}
    assert connect.await_args_list[1].kwargs == {"use_services_cache": False}


@pytest.mark.asyncio
async def test_status_read_normalizes_missing_fresh_characteristic() -> None:
    clients = [FakeClient(), FakeClient()]
    for client in clients:
        client.write_gatt_char = AsyncMock(
            side_effect=BleakCharacteristicNotFoundError(transport.SESSION_UUID)
        )

    with patch.object(transport, "_connect", AsyncMock(side_effect=clients)):
        with pytest.raises(transport.WeberBluetoothError, match="could not be discovered"):
            await transport.async_read_status(
                SimpleNamespace(),
                ADDRESS,
                IDENTITY.companion_id,
                10,
                timeout=0.5,
            )

    assert all(client.disconnected for client in clients)

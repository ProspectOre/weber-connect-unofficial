"""Read-only portable-probe protocol, GATT, and integration contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.exc import BleakError
from homeassistant.const import CONF_ADDRESS

from custom_components.weber_connect import coordinator as coordinator_module
from custom_components.weber_connect import entity as entity_module
from custom_components.weber_connect import portable
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.const import CONF_MODEL_ID, CONF_TRANSPORT_FAMILY
from custom_components.weber_connect.coordinator import WeberCoordinator
from custom_components.weber_connect.entity import WeberEntity, known_probe_numbers
from custom_components.weber_connect.transport_family import PORTABLE_TEMPERATURE_UUID


def reading(
    *deci_c_values: int,
    model_sequence: int = (3 << 28) | 0x01234567,
    battery: int = 88,
) -> bytes:
    return (
        model_sequence.to_bytes(4, "little")
        + bytes((battery, len(deci_c_values)))
        + b"".join(value.to_bytes(2, "little", signed=True) for value in deci_c_values)
    )


def test_portable_parser_decodes_header_sentinel_and_signed_deci_c() -> None:
    parsed = portable.parse_portable_reading(
        reading(-32768, -123, 254, model_sequence=(5 << 28) | 0x0ABCDEF), 16
    )

    assert parsed["sequence"] == 0x0ABCDEF
    assert parsed["boot_count"] == 5
    assert parsed["sensor_count"] == 3
    assert parsed["battery_level"] == 88
    assert parsed["device_state"] == "on"
    assert parsed["probes"] == [
        {
            "probe_number": 1,
            "probe_temp_c": 25.4,
            "ambient_temp_c": 25.4,
            "battery_level": 88,
            "probe_type": "ambient",
        }
    ]


def test_wasp_uses_ambient_and_batwoman_uses_coldest_core_sensor() -> None:
    payload = reading(380, -123, 210)

    wasp = portable.parse_portable_reading(payload, 16)["probes"][0]
    batwoman = portable.parse_portable_reading(payload, 17)["probes"][0]

    assert wasp["probe_temp_c"] == 21.0
    assert wasp["probe_type"] == "ambient"
    assert batwoman["probe_temp_c"] == -12.3
    assert batwoman["ambient_temp_c"] == 21.0
    assert batwoman["probe_type"] == "wireless"


def test_portable_parser_preserves_missing_ambient_sensor_and_rejects_bad_battery() -> None:
    parsed = portable.parse_portable_reading(reading(330, -32768, battery=101), 17)

    assert parsed["battery_level"] is None
    assert parsed["probes"][0]["probe_temp_c"] == 33.0
    assert parsed["probes"][0]["ambient_temp_c"] is None
    assert parsed["probes"][0]["battery_level"] is None


@pytest.mark.parametrize("model_id", [0, 15, 18, 999])
def test_portable_parser_rejects_unknown_model(model_id: int) -> None:
    with pytest.raises(ValueError, match="model or header"):
        portable.parse_portable_reading(reading(200), model_id)


@pytest.mark.parametrize("payload", [b"", b"12345", reading(200)[:-1], reading(*([200] * 17))])
def test_portable_parser_rejects_short_or_inconsistent_sensor_count(payload: bytes) -> None:
    with pytest.raises(ValueError, match="Portable"):
        portable.parse_portable_reading(payload, 16)


class FakeGattChannel:
    outcomes: ClassVar[list[bytes | BaseException]] = []
    instances: ClassVar[list[FakeGattChannel]] = []

    def __init__(self, client: object, characteristic: str) -> None:
        self.client = client
        self.characteristic = characteristic
        self.opened = False
        self.instances.append(self)

    async def async_open(self) -> None:
        self.opened = True

    async def async_receive(self) -> bytes:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def reset_fake_gatt_channel() -> None:
    FakeGattChannel.outcomes = []
    FakeGattChannel.instances = []


@pytest.mark.asyncio
async def test_one_shot_gatt_read_opens_read_only_channel_and_disconnects() -> None:
    hass = object()
    client = object()
    payload = reading(450, 230)
    FakeGattChannel.outcomes = [payload]
    connect = AsyncMock(return_value=client)
    disconnect = AsyncMock()
    with (
        patch.object(portable, "_connect", connect),
        patch.object(portable, "_safe_disconnect", disconnect),
        patch.object(portable, "GattResponseChannel", FakeGattChannel),
    ):
        result = await portable.async_read_portable(hass, "AA:BB", 17)  # type: ignore[arg-type]

    connect.assert_awaited_once_with(hass, "AA:BB", use_services_cache=False)
    channel = FakeGattChannel.instances[0]
    assert channel.characteristic == PORTABLE_TEMPERATURE_UUID
    assert channel.opened is True
    assert result["probes"][0]["probe_temp_c"] == 45.0
    disconnect.assert_awaited_once_with(client)


@pytest.mark.parametrize(
    "outcome",
    [
        BleakError("radio failed"),
        TimeoutError("read timed out"),
        ValueError("invalid local frame"),
    ],
)
@pytest.mark.asyncio
async def test_one_shot_gatt_read_wraps_receive_errors_and_disconnects(
    outcome: BaseException,
) -> None:
    client = object()
    FakeGattChannel.outcomes = [outcome]
    disconnect = AsyncMock()
    with (
        patch.object(portable, "_connect", AsyncMock(return_value=client)),
        patch.object(portable, "_safe_disconnect", disconnect),
        patch.object(portable, "GattResponseChannel", FakeGattChannel),
    ):
        with pytest.raises(WeberBluetoothError, match="complete reading"):
            await portable.async_read_portable(object(), "AA:BB", 16)  # type: ignore[arg-type]

    disconnect.assert_awaited_once_with(client)


@pytest.mark.asyncio
async def test_one_shot_gatt_read_wraps_connect_error_without_disconnect() -> None:
    disconnect = AsyncMock()
    with (
        patch.object(portable, "_connect", AsyncMock(side_effect=BleakError("offline"))),
        patch.object(portable, "_safe_disconnect", disconnect),
    ):
        with pytest.raises(WeberBluetoothError, match="complete reading"):
            await portable.async_read_portable(object(), "AA:BB", 16)  # type: ignore[arg-type]

    disconnect.assert_not_awaited()


class SessionChannel:
    """Channel script for a session reconnect, then one complete local reading."""

    def __init__(self, client: object, characteristic: str) -> None:
        self.client = client
        self.characteristic = characteristic
        self.index = len(self.__class__.created)
        self.__class__.created.append(self)

    created: ClassVar[list[SessionChannel]] = []
    first_error: ClassVar[BaseException | None] = BleakError("notification ended")

    async def async_open(self) -> None:
        return None

    async def async_receive(self) -> bytes:
        if self.index == 0:
            assert self.first_error is not None
            raise self.first_error
        # Give the callback's scheduled close a chance to run before the
        # session begins its next receive iteration.
        await asyncio.sleep(0)
        return reading(480, 220)


@pytest.mark.asyncio
async def test_portable_session_reports_read_reconnects_and_closes_every_client() -> None:
    SessionChannel.created = []
    SessionChannel.first_error = BleakError("notification ended")
    clients = [object(), object()]
    connect = AsyncMock(side_effect=clients)
    disconnect = AsyncMock()
    session = portable.PortableSession(object(), "AA:BB", 17)  # type: ignore[arg-type]
    statuses: list[dict[str, Any]] = []
    errors: list[str] = []

    def status_callback(value: dict[str, Any]) -> None:
        statuses.append(value)
        asyncio.get_running_loop().create_task(session.async_close())

    def error_callback(message: str) -> None:
        errors.append(message)
        session.async_wake()

    with (
        patch.object(portable, "_connect", connect),
        patch.object(portable, "_safe_disconnect", disconnect),
        patch.object(portable, "GattResponseChannel", SessionChannel),
    ):
        await asyncio.wait_for(session.async_run(status_callback, error_callback), timeout=1)

    assert errors == ["The portable Bluetooth reading is unavailable."]
    assert len(statuses) == 1
    assert statuses[0]["probes"][0]["probe_temp_c"] == 48.0
    assert connect.await_count == 2
    assert all(call.kwargs == {"use_services_cache": False} for call in connect.await_args_list)
    assert [channel.characteristic for channel in SessionChannel.created] == [
        PORTABLE_TEMPERATURE_UUID,
        PORTABLE_TEMPERATURE_UUID,
    ]
    assert [call.args[0] for call in disconnect.await_args_list] == clients


@pytest.mark.asyncio
async def test_portable_session_task_cancellation_disconnects_during_pending_read() -> None:
    client = object()
    disconnect = AsyncMock()
    receive_started = asyncio.Event()

    class BlockingChannel:
        def __init__(self, *_args: object) -> None:
            pass

        async def async_open(self) -> None:
            return None

        async def async_receive(self) -> bytes:
            receive_started.set()
            await asyncio.Future()

    session = portable.PortableSession(object(), "AA:BB", 16)  # type: ignore[arg-type]
    with (
        patch.object(portable, "_connect", AsyncMock(return_value=client)),
        patch.object(portable, "_safe_disconnect", disconnect),
        patch.object(portable, "GattResponseChannel", BlockingChannel),
    ):
        task = asyncio.create_task(session.async_run(lambda _value: None, lambda _error: None))
        await asyncio.wait_for(receive_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    disconnect.assert_awaited_once_with(client)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_portable_coordinator_needs_no_cloud_credentials(hass: object) -> None:
    entry = SimpleNamespace(
        data={
            CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_MODEL_ID: 17,
            CONF_TRANSPORT_FAMILY: "portable",
        },
        options={},
        entry_id="portable-entry",
        unique_id="portable-device",
        title="Portable Probe",
        pref_disable_polling=False,
        async_on_unload=Mock(),
        async_create_background_task=lambda _hass, coro, name: hass.async_create_task(  # type: ignore[attr-defined]
            coro, name=name
        ),
    )
    transport = Mock()
    with (
        patch.object(coordinator_module, "PortableSession", return_value=transport) as session,
        patch.object(coordinator_module, "WeberCloudClient") as cloud_client,
        patch.object(coordinator_module, "WeberCloudSession") as cloud_session,
    ):
        coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]

    session.assert_called_once_with(hass, "AA:BB:CC:DD:EE:FF", 17)
    cloud_client.assert_not_called()
    cloud_session.assert_not_called()
    assert coordinator.source == "bluetooth"
    assert coordinator._transport is transport
    assert coordinator.cloud_client is None
    assert coordinator.cloud_session is None
    assert coordinator.initial_state()["source"] == "bluetooth"


def test_known_portable_probe_slot_is_available_without_registry_lookup() -> None:
    entry = SimpleNamespace(
        data={CONF_TRANSPORT_FAMILY: "portable"}, entry_id="portable-entry", unique_id="id"
    )
    with patch.object(entity_module.er, "async_get", side_effect=AssertionError("unused")):
        assert known_probe_numbers(object(), entry) == {1}  # type: ignore[arg-type]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_portable_entity_uses_portable_device_model(hass: object) -> None:
    entry = SimpleNamespace(
        data={
            CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_MODEL_ID: 16,
            CONF_TRANSPORT_FAMILY: "portable",
        },
        options={},
        entry_id="portable-entry",
        unique_id="portable-device",
        title="Kitchen Probe",
        pref_disable_polling=False,
        async_on_unload=Mock(),
        async_create_background_task=lambda _hass, coro, name: hass.async_create_task(  # type: ignore[attr-defined]
            coro, name=name
        ),
    )
    with patch.object(coordinator_module, "PortableSession", return_value=Mock()):
        coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]
    entity = WeberEntity(coordinator, entry, "probe_1_temperature")  # type: ignore[arg-type]

    assert entity._attr_device_info["model"] == "Portable Smart Probe"
    assert entity._attr_unique_id == "portable-device_probe_1_temperature"

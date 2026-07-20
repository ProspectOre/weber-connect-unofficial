"""Coordinator and diagnostics tests for transport failover."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers import issue_registry as ir

from custom_components.weber_connect import coordinator as coordinator_module
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.const import (
    CONF_ADVANCED,
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_COMPANION_PRIVATE_KEY,
    CONF_COMPANION_PUBLIC_KEY,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_LOCAL_FALLBACK,
)
from custom_components.weber_connect.coordinator import WeberCoordinator
from custom_components.weber_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.weber_connect.models import WeberRuntimeData
from custom_components.weber_connect.options import ConnectionMode, WeberOptions
from custom_components.weber_connect.weber_cloud import WeberCloudError

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry(*, cloud: bool = True, local_fallback: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_COMPANION_ID: "11" * 16,
            CONF_CLOUD_PASSWORD: "cloud-password",
            CONF_COMPANION_PRIVATE_KEY: "private-key",
            CONF_COMPANION_PUBLIC_KEY: "public-key",
        },
        options={
            CONF_CONNECTION: {
                CONF_CONNECTION_MODE: (
                    ConnectionMode.PHONE_AND_HOME_ASSISTANT
                    if cloud
                    else ConnectionMode.HOME_ASSISTANT_ONLY
                ),
            },
            CONF_ADVANCED: {CONF_LOCAL_FALLBACK: local_fallback},
        },
        entry_id="test-entry",
        unique_id="AA:BB:CC:DD:EE:FF",
        title="Test Weber Hub",
        async_create_background_task=MagicMock(),
        async_on_unload=MagicMock(),
    )


class FakeCloudClient:
    """Synchronous cloud double executed through Home Assistant's executor."""

    def __init__(self, config: object) -> None:
        self.config = config
        self.closed = False

    def associated_appliances(self) -> list[dict[str, str]]:
        return [{"oven_id": "22" * 16}]

    def poll(self, appliance_id: str) -> SimpleNamespace:
        assert appliance_id == "22" * 16
        return SimpleNamespace(
            status={
                "probes": [
                    {
                        "probe_number": 1,
                        "probe_temp_c": 63.5,
                        "battery_level": 82,
                        "state": "Connected",
                    }
                ]
            }
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_cloud_update_and_close(hass: object) -> None:
    with patch.object(coordinator_module, "WeberCloudClient", FakeCloudClient):
        coordinator = WeberCoordinator(hass, _entry())  # type: ignore[arg-type]

    state = await coordinator._async_update_data()
    assert state["source"] == "cloud"
    assert state["cloud_ready"] is True
    assert state["probe_1_temperature"] == 63.5
    assert coordinator.appliance_id == "22" * 16

    client = coordinator.cloud_client
    assert isinstance(client, FakeCloudClient)

    await coordinator.async_close()
    assert client.closed is True


@pytest.mark.asyncio
async def test_live_loop_subtracts_request_time_from_interval(hass: object) -> None:
    entry = _entry(cloud=False)
    coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]
    coordinator.poll_seconds = 10
    coordinator.async_refresh = AsyncMock(side_effect=[None, asyncio.CancelledError])

    monotonic = iter((100.0, 104.0, 110.0))
    with (
        patch.object(asyncio, "get_running_loop") as get_loop,
        patch.object(asyncio, "sleep", AsyncMock()) as sleep,
    ):
        get_loop.return_value.time.side_effect = lambda: next(monotonic)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_poll_loop()

    sleep.assert_awaited_once_with(6.0)


@pytest.mark.asyncio
async def test_live_loop_keeps_a_cooldown_after_a_slow_request(hass: object) -> None:
    entry = _entry(cloud=False)
    coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]
    coordinator.poll_seconds = 10
    coordinator.async_refresh = AsyncMock(side_effect=[None, asyncio.CancelledError])

    monotonic = iter((100.0, 114.0, 115.0))
    with (
        patch.object(asyncio, "get_running_loop") as get_loop,
        patch.object(asyncio, "sleep", AsyncMock()) as sleep,
    ):
        get_loop.return_value.time.side_effect = lambda: next(monotonic)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_poll_loop()

    sleep.assert_awaited_once_with(1.0)


def test_async_start_is_idempotent_and_entry_scoped(hass: object) -> None:
    entry = _entry(cloud=False)
    task = MagicMock()
    entry.async_create_background_task.return_value = task
    coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]

    cancel_callback = MagicMock()
    with patch.object(
        coordinator_module.bluetooth,
        "async_register_callback",
        return_value=cancel_callback,
    ) as register:
        coordinator.async_start()
        coordinator.async_start()

    entry.async_create_background_task.assert_called_once()
    register.assert_called_once_with(
        hass,
        coordinator._async_bluetooth_advertisement,
        {"address": coordinator.address, "connectable": True},
        coordinator_module.bluetooth.BluetoothScanningMode.ACTIVE,
    )
    assert coordinator._cancel_bluetooth_callback is cancel_callback
    assert coordinator._poll_task is task
    entry.async_create_background_task.call_args.args[1].close()


def test_bluetooth_advertisement_schedules_one_immediate_refresh(hass: object) -> None:
    entry = _entry(cloud=False)
    poll_task = MagicMock()
    refresh_task = MagicMock()
    refresh_task.done.return_value = False
    entry.async_create_background_task.side_effect = [poll_task, refresh_task]
    coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]

    with patch.object(coordinator_module.bluetooth, "async_register_callback") as register:
        coordinator.async_start()
    advertisement_callback = register.call_args.args[1]
    advertisement_callback(MagicMock(), MagicMock())
    advertisement_callback(MagicMock(), MagicMock())

    assert entry.async_create_background_task.call_count == 2
    assert coordinator._advertisement_refresh_task is refresh_task
    refresh_task.add_done_callback.assert_called_once_with(
        coordinator._async_bluetooth_advertisement_refresh_done
    )
    for call in entry.async_create_background_task.call_args_list:
        call.args[1].close()


@pytest.mark.asyncio
async def test_async_close_cancels_bluetooth_wake_work(hass: object) -> None:
    entry = _entry(cloud=False)
    coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]
    cancel_callback = MagicMock()
    wake_task = asyncio.create_task(asyncio.sleep(60))
    coordinator._cancel_bluetooth_callback = cancel_callback
    coordinator._advertisement_refresh_task = wake_task

    await coordinator.async_close()

    cancel_callback.assert_called_once_with()
    assert wake_task.cancelled()
    assert coordinator._advertisement_refresh_task is None


@pytest.mark.asyncio
async def test_cloud_failure_uses_local_fallback(hass: object) -> None:
    with patch.object(coordinator_module, "WeberCloudClient", FakeCloudClient):
        coordinator = WeberCoordinator(
            hass,
            _entry(local_fallback=True),  # type: ignore[arg-type]
        )
    coordinator._async_cloud_update = AsyncMock(side_effect=WeberCloudError("not linked"))
    coordinator._async_bluetooth_update = AsyncMock(
        return_value={"source": "bluetooth", "connected": True}
    )

    state = await coordinator._async_update_data()

    assert state == {"source": "bluetooth", "connected": True}
    assert coordinator.last_error is None
    assert coordinator.successful_updates == 1
    assert coordinator.failed_updates == 0


@pytest.mark.asyncio
async def test_transport_failures_return_stable_offline_state(hass: object) -> None:
    with patch.object(coordinator_module, "WeberCloudClient", FakeCloudClient):
        cloud_only = WeberCoordinator(hass, _entry())  # type: ignore[arg-type]
    cloud_only._async_cloud_update = AsyncMock(side_effect=WeberCloudError("not linked"))

    cloud_state = await cloud_only._async_update_data()
    assert cloud_state["source"] == "cloud"
    assert cloud_state["connected"] is False
    assert cloud_only.last_error == "not linked"

    bluetooth_only = WeberCoordinator(
        hass,
        _entry(cloud=False),  # type: ignore[arg-type]
    )
    bluetooth_only._async_bluetooth_update = AsyncMock(
        side_effect=WeberBluetoothError("out of range")
    )

    bluetooth_state = await bluetooth_only._async_update_data()
    assert bluetooth_state["source"] == "bluetooth"
    assert bluetooth_state["connected"] is False
    assert bluetooth_only.last_error == "out of range"


@pytest.mark.asyncio
async def test_bluetooth_update_leaves_proxy_deadline_to_connector(hass: object) -> None:
    """Do not cancel Home Assistant while a proxy is allocating its GATT slot."""

    coordinator = WeberCoordinator(
        hass,
        _entry(cloud=False),  # type: ignore[arg-type]
    )

    status = {
        "kind": "cook_session_status",
        "probes": [{"probe_number": 1, "probe_temp_c": 25.0, "state": "Probed"}],
    }

    with (
        patch.object(
            coordinator_module.asyncio,
            "timeout",
            side_effect=AssertionError("coordinator must not wrap the connector timeout"),
        ),
        patch.object(coordinator_module, "async_read_status", AsyncMock(return_value=status)),
    ):
        result = await coordinator._async_bluetooth_update()

    assert result["connected"] is True
    assert result["probe_1_temperature"] == 25.0


@pytest.mark.asyncio
async def test_transient_failure_preserves_last_valid_readings(hass: object) -> None:
    coordinator = WeberCoordinator(
        hass,
        _entry(cloud=False),  # type: ignore[arg-type]
    )
    previous = {
        "updated_at": "2026-07-19T20:00:00+00:00",
        "connected": True,
        "cloud_ready": False,
        "source": "bluetooth",
        "probe_4_temperature": 25.0,
        "probe_4_state": "Probed",
    }
    coordinator.data = previous
    coordinator.last_successful_update = "2026-07-19T20:00:00+00:00"
    coordinator._async_bluetooth_update = AsyncMock(
        side_effect=WeberBluetoothError("temporary proxy interruption")
    )

    state = await coordinator._async_update_data()

    assert state["connected"] is True
    assert state["probe_4_temperature"] == 25.0
    assert state["probe_4_state"] == "Probed"
    assert state["updated_at"] == previous["updated_at"]
    assert coordinator.last_error == "temporary proxy interruption"
    assert coordinator.successful_updates == 0
    assert coordinator.failed_updates == 1


@pytest.mark.asyncio
async def test_sustained_failures_mark_preserved_readings_offline(hass: object) -> None:
    coordinator = WeberCoordinator(
        hass,
        _entry(cloud=False),  # type: ignore[arg-type]
    )
    coordinator.data = {
        "updated_at": "2026-07-19T20:00:00+00:00",
        "connected": True,
        "cloud_ready": False,
        "source": "bluetooth",
        "probe_4_temperature": 25.0,
        "probe_4_state": "Probed",
    }
    coordinator.last_successful_update = "2026-07-19T20:00:00+00:00"
    coordinator._async_bluetooth_update = AsyncMock(
        side_effect=WeberBluetoothError("sustained proxy interruption")
    )

    for _ in range(coordinator_module.OFFLINE_FAILURE_THRESHOLD):
        state = await coordinator._async_update_data()

    assert state["connected"] is False
    assert state["probe_4_temperature"] == 25.0
    assert coordinator.consecutive_failures == coordinator_module.OFFLINE_FAILURE_THRESHOLD
    assert coordinator.successful_updates == 0
    assert coordinator.failed_updates == coordinator_module.OFFLINE_FAILURE_THRESHOLD


@pytest.mark.asyncio
async def test_sustained_outage_creates_and_recovery_clears_repair(hass: object) -> None:
    with patch.object(coordinator_module, "WeberCloudClient", FakeCloudClient):
        coordinator = WeberCoordinator(hass, _entry())  # type: ignore[arg-type]
    coordinator._async_cloud_update = AsyncMock(side_effect=WeberCloudError("offline"))

    for _ in range(6):
        await coordinator._async_update_data()

    issue_id = f"connection_lost_{coordinator.entry.entry_id}"
    assert ir.async_get(hass).async_get_issue("weber_connect", issue_id) is not None

    coordinator._async_cloud_update = AsyncMock(return_value={"source": "cloud", "connected": True})
    await coordinator._async_update_data()
    assert ir.async_get(hass).async_get_issue("weber_connect", issue_id) is None


@pytest.mark.asyncio
async def test_diagnostics_redact_all_private_material(hass: object) -> None:
    coordinator = SimpleNamespace(
        data={"appliance_public_key": "appliance-key", "connected": True},
        options=WeberOptions(),
        poll_seconds=10,
        last_successful_update="2026-07-18T12:00:00+00:00",
        consecutive_failures=1,
        successful_updates=12,
        failed_updates=1,
        last_error="temporary failure",
        cloud_client=SimpleNamespace(
            socket_error="live session unavailable",
            _socket_client=SimpleNamespace(received_types=[0x83]),
            session_schema={"fields": ["session_id", "status"]},
            snapshot_schema={"fields": ["data", "snapshot_id"]},
        ),
    )
    entry = _entry()
    entry.data[CONF_APPLIANCE_ID] = "22" * 16
    entry.runtime_data = WeberRuntimeData(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(
        hass,
        entry,  # type: ignore[arg-type]
    )

    assert diagnostics["entry"][CONF_ADDRESS] != "AA:BB:CC:DD:EE:FF"
    assert diagnostics["entry"][CONF_APPLIANCE_ID] != "22" * 16
    assert diagnostics["entry"][CONF_COMPANION_ID] != "11" * 16
    assert diagnostics["entry"][CONF_CLOUD_PASSWORD] != "cloud-password"
    assert diagnostics["entry"][CONF_COMPANION_PRIVATE_KEY] != "private-key"
    assert diagnostics["entry"][CONF_COMPANION_PUBLIC_KEY] != "public-key"
    assert diagnostics["state"]["appliance_public_key"] != "appliance-key"
    assert diagnostics["state"]["connected"] is True
    assert diagnostics["successful_updates"] == 12
    assert diagnostics["failed_updates"] == 1
    assert diagnostics["last_error"] == "temporary failure"
    assert diagnostics["cloud_live_error"] == "live session unavailable"
    assert diagnostics["cloud_socket_received_types"] == [0x83]
    assert diagnostics["cloud_history_schema"]["session"]["fields"] == [
        "session_id",
        "status",
    ]

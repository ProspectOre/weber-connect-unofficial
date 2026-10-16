"""Coordinator lifecycle tests for background SiLabs capability refresh."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect import coordinator as coordinator_module
from custom_components.weber_connect import entity as entity_module
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.const import (
    CONF_APPLIANCE_ID,
    CONF_CAPABILITIES,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_MODEL_ID,
    CONF_PAIRING_KEY_REF,
    CONF_TRANSPORT_FAMILY,
    DOMAIN,
)
from custom_components.weber_connect.coordinator import WeberCoordinator
from custom_components.weber_connect.transport_family import TransportFamily

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ADDRESS = "AA:BB:CC:DD:EE:FF"
COMPANION = "11" * 16
APPLIANCE = "22" * 16
PAIRING_REFERENCE = "33" * 16


class FakeTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = False
        self.status_callback: Any = None
        self.error_callback: Any = None

    async def async_run(self, status_callback: Any, error_callback: Any) -> None:
        self.status_callback = status_callback
        self.error_callback = error_callback
        self.started.set()
        await asyncio.Future()

    def async_wake(self) -> None:
        return None

    async def async_close(self) -> None:
        self.closed = True


class FakeCloudClient:
    def __init__(self, _config: object) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _entry(
    hass: object,
    *,
    family: str = "silabs_appliance",
    reference: str | None = PAIRING_REFERENCE,
) -> MockConfigEntry:
    data: dict[str, Any] = {
        CONF_ADDRESS: ADDRESS,
        CONF_TRANSPORT_FAMILY: family,
        CONF_MODEL_ID: 1,
        CONF_COMPANION_ID: COMPANION,
        CONF_CLOUD_PASSWORD: "synthetic-password",
        CONF_APPLIANCE_ID: APPLIANCE,
    }
    if reference is not None:
        data[CONF_PAIRING_KEY_REF] = reference
    entry = MockConfigEntry(domain=DOMAIN, data=data, options={}, unique_id="test-appliance")
    entry.add_to_hass(hass)  # type: ignore[arg-type]
    return entry


def _coordinator(
    hass: object, entry: MockConfigEntry, transport: FakeTransport | None = None
) -> tuple[WeberCoordinator, FakeTransport, FakeCloudClient]:
    session = transport or FakeTransport()
    with (
        patch.object(coordinator_module, "WeberCloudClient", FakeCloudClient),
        patch.object(coordinator_module, "WeberCloudSession", return_value=session),
    ):
        coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]
    assert isinstance(coordinator.cloud_client, FakeCloudClient)
    return coordinator, session, coordinator.cloud_client


@pytest.mark.asyncio
async def test_start_runs_cloud_and_independent_capability_refresh_and_close_cancels_both(
    hass: object,
) -> None:
    entry = _entry(hass)
    coordinator, transport, cloud_client = _coordinator(hass, entry)
    reconnect_started = asyncio.Event()
    reconnect_cancelled = asyncio.Event()
    never_finish = asyncio.Event()

    async def blocked_reconnect(*_args: object) -> dict[str, int]:
        reconnect_started.set()
        try:
            await never_finish.wait()
        except asyncio.CancelledError:
            reconnect_cancelled.set()
            raise
        return {"probe_count": 2}

    reconnect = AsyncMock(side_effect=blocked_reconnect)

    with (
        patch.object(coordinator_module, "async_reconnect_silabs", reconnect),
        patch.object(coordinator_module.ir, "async_get", return_value=SimpleNamespace(issues={})),
    ):
        coordinator.async_start()
        await asyncio.wait_for(transport.started.wait(), timeout=1)
        await asyncio.wait_for(reconnect_started.wait(), timeout=1)
        transport_task = coordinator._transport_task
        capability_task = coordinator._capability_task
        assert transport_task is not None
        assert capability_task is not None
        reconnect.assert_awaited_once_with(hass, ADDRESS, PAIRING_REFERENCE, APPLIANCE)

        await coordinator.async_close()

    assert transport.closed is True
    assert cloud_client.closed is True
    assert transport_task.cancelled()
    assert capability_task.cancelled()
    assert reconnect_cancelled.is_set()
    assert coordinator._transport_task is None
    assert coordinator._capability_task is None


@pytest.mark.parametrize(
    ("family", "reference"),
    [("saber", PAIRING_REFERENCE), ("silabs_appliance", None)],
)
@pytest.mark.asyncio
async def test_start_skips_capability_refresh_without_supported_pairing_context(
    hass: object, family: str, reference: str | None
) -> None:
    entry = _entry(hass, family=family, reference=reference)
    coordinator, transport, _client = _coordinator(hass, entry)
    with (
        patch.object(coordinator_module, "async_reconnect_silabs", new=AsyncMock()) as reconnect,
        patch.object(coordinator_module.ir, "async_get", return_value=SimpleNamespace(issues={})),
    ):
        coordinator.async_start()
        await asyncio.wait_for(transport.started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert coordinator._capability_task is None
        reconnect.assert_not_awaited()
        await coordinator.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_count, expected", [(3, (1, 2, 3)), (8, (1, 2, 3, 4))])
async def test_refresh_persists_capabilities_and_emits_only_bounded_probe_slots(
    hass: object, probe_count: int, expected: tuple[int, ...]
) -> None:
    entry = _entry(hass)
    coordinator, _transport, _client = _coordinator(hass, entry)
    found = {"probe_count": probe_count, "region": 1}
    reconnect = AsyncMock(return_value=found)
    with patch.object(coordinator_module, "async_reconnect_silabs", new=reconnect):
        await coordinator._async_refresh_capabilities()

    reconnect.assert_awaited_once_with(hass, ADDRESS, PAIRING_REFERENCE, APPLIANCE)
    assert entry.data[CONF_CAPABILITIES] == found
    assert coordinator.data["reported_probe_numbers"] == expected


@pytest.mark.asyncio
async def test_unchanged_capabilities_do_not_persist_or_emit_an_update(hass: object) -> None:
    capabilities = {"probe_count": 2, "region": 1}
    entry = _entry(hass)
    hass.config_entries.async_update_entry(  # type: ignore[attr-defined]
        entry, data={**entry.data, CONF_CAPABILITIES: capabilities}
    )
    coordinator, _transport, _client = _coordinator(hass, entry)
    reconnect = AsyncMock(return_value=capabilities)
    with (
        patch.object(coordinator_module, "async_reconnect_silabs", new=reconnect),
        patch.object(hass.config_entries, "async_update_entry") as update_entry,  # type: ignore[attr-defined]
        patch.object(coordinator, "async_set_updated_data") as publish,
    ):
        await coordinator._async_refresh_capabilities()

    update_entry.assert_not_called()
    publish.assert_not_called()
    reconnect.assert_awaited_once()


@pytest.mark.parametrize("failure", [WeberBluetoothError("BLE unavailable"), TimeoutError()])
@pytest.mark.asyncio
async def test_refresh_ble_or_timeout_failure_does_not_mark_cloud_transport_offline(
    hass: object, failure: BaseException
) -> None:
    entry = _entry(hass)
    coordinator, _transport, _client = _coordinator(hass, entry)
    coordinator.async_set_updated_data(
        {
            "connected": True,
            "reading_status": "receiving",
            "probe_1_temperature": 48.0,
        }
    )
    with (
        patch.object(
            coordinator_module,
            "async_reconnect_silabs",
            new=AsyncMock(side_effect=failure),
        ),
        patch.object(coordinator, "async_set_updated_data") as publish,
    ):
        await coordinator._async_refresh_capabilities()

    assert coordinator.data["connected"] is True
    assert coordinator.data["reading_status"] == "receiving"
    assert coordinator.data["probe_1_temperature"] == 48.0
    assert coordinator.consecutive_failures == 0
    assert coordinator.failed_updates == 0
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_replaced_pairing_reference_after_reconnect_cannot_overwrite_entry(
    hass: object,
) -> None:
    entry = _entry(hass)
    coordinator, _transport, _client = _coordinator(hass, entry)
    found = {"probe_count": 4}

    async def replace_reference(*_args: object) -> dict[str, int]:
        hass.config_entries.async_update_entry(  # type: ignore[attr-defined]
            entry,
            data={**entry.data, CONF_PAIRING_KEY_REF: "44" * 16},
        )
        return found

    with (
        patch.object(coordinator_module, "async_reconnect_silabs", replace_reference),
        patch.object(
            hass.config_entries,
            "async_update_entry",
            wraps=hass.config_entries.async_update_entry,
        ) as update_entry,  # type: ignore[attr-defined]
        patch.object(coordinator, "async_set_updated_data") as publish,
    ):
        await coordinator._async_refresh_capabilities()

    assert update_entry.call_count == 1
    assert entry.data[CONF_PAIRING_KEY_REF] == "44" * 16
    assert CONF_CAPABILITIES not in entry.data
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_portable_coordinator_closes_without_cloud_client(hass: object) -> None:
    entry = _entry(hass, family=TransportFamily.PORTABLE.value, reference=None)
    hass.config_entries.async_update_entry(
        entry,
        data={
            key: value
            for key, value in entry.data.items()
            if key not in (CONF_COMPANION_ID, CONF_CLOUD_PASSWORD, CONF_APPLIANCE_ID)
        },
    )
    portable_session = FakeTransport()
    with patch.object(coordinator_module, "PortableSession", return_value=portable_session):
        coordinator = WeberCoordinator(hass, entry)  # type: ignore[arg-type]

    assert coordinator.cloud_client is None
    await coordinator.async_close()
    assert portable_session.closed is True


@pytest.mark.parametrize(
    ("count", "registered", "expected"),
    [
        (0, (), set()),
        (4, (), {1, 2, 3, 4}),
        (8, (), {1, 2, 3, 4}),
        (0, (3,), {3}),
    ],
)
def test_known_probe_numbers_bounds_capabilities_and_keeps_registered_slots(
    hass: object, count: int, registered: tuple[int, ...], expected: set[int]
) -> None:
    entry = _entry(hass)
    identity = entry.unique_id or entry.entry_id
    entities = [
        SimpleNamespace(unique_id=f"{identity}_probe_{number}_temperature") for number in registered
    ]
    hass.config_entries.async_update_entry(  # type: ignore[attr-defined]
        entry, data={**entry.data, CONF_CAPABILITIES: {"probe_count": count}}
    )
    with (
        patch.object(entity_module.er, "async_get", return_value=object()),
        patch.object(entity_module.er, "async_entries_for_config_entry", return_value=entities),
    ):
        assert entity_module.known_probe_numbers(hass, entry) == expected  # type: ignore[arg-type]

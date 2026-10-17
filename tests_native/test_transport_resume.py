"""Coordinator lifecycle tests for authenticated cloud telemetry."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import ConfigEntryError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect import coordinator as coordinator_module
from custom_components.weber_connect import entity as entity_module
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
async def test_start_uses_only_cloud_transport_even_with_stored_pairing_keys(hass: object) -> None:
    entry = _entry(hass)
    coordinator, transport, cloud_client = _coordinator(hass, entry)
    with (
        patch("custom_components.weber_connect.bluetooth.async_reconnect_silabs") as reconnect,
        patch.object(coordinator_module.ir, "async_get", return_value=SimpleNamespace(issues={})),
    ):
        coordinator.async_start()
        await asyncio.wait_for(transport.started.wait(), timeout=1)
        transport_task = coordinator._transport_task
        assert transport_task is not None
        reconnect.assert_not_called()
        await coordinator.async_close()

    assert transport.closed is True
    assert cloud_client.closed is True
    assert transport_task.cancelled()
    assert coordinator._transport_task is None


@pytest.mark.asyncio
async def test_portable_coordinator_is_rejected_until_authenticated_telemetry_exists(
    hass: object,
) -> None:
    entry = _entry(hass, family="portable", reference=None)
    with pytest.raises(ConfigEntryError, match="verified authentication"):
        _coordinator(hass, entry)


@pytest.mark.parametrize(
    ("count", "registered", "expected"),
    [
        (0, (), set()),
        (4, (), {1, 2, 3, 4}),
        (8, (), set(range(1, 9))),
        (99, (), set(range(1, 17))),
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

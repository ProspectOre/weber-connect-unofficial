"""Shared entity base for Weber Connect."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WeberCoordinator


def build_entity_unique_id(identity: str, key: str) -> str:
    """Build a stable identity from the hub and physical slot."""

    return f"{identity}_{key}"


def known_probe_numbers(hass: HomeAssistant, entry: ConfigEntry) -> set[int]:
    """Keep known physical slots available even before new telemetry arrives."""

    identity = entry.unique_id or entry.entry_id
    registered = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    return {1, 2} | {
        number
        for number in (3, 4)
        if any(
            build_entity_unique_id(identity, f"probe_{number}_{suffix}") in registered
            for suffix in ("temperature", "reading_status")
        )
    }


class WeberEntity(CoordinatorEntity[WeberCoordinator]):
    """Base class tying every entity to one stable Weber hub device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WeberCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        identity = entry.unique_id or entry.entry_id
        self._attr_unique_id = build_entity_unique_id(identity, key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            connections={(CONNECTION_BLUETOOTH, str(entry.data[CONF_ADDRESS]))},
            manufacturer=MANUFACTURER,
            model="Connect Smart Grilling Hub",
            name=entry.title,
        )

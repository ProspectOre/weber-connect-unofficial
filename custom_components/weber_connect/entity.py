"""Shared entity base for Weber Connect."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import WeberCoordinator


def build_entity_unique_id(identity: str, key: str) -> str:
    """Build a stable identity from the hub and physical slot."""

    return f"{identity}_{key}"


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

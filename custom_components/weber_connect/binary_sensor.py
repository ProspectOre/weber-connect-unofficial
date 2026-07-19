"""Connection entities for Weber Connect."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WeberCoordinator
from .entity import WeberEntity
from .models import WeberRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: WeberRuntimeData = entry.runtime_data
    async_add_entities((WeberConnectionEntity(runtime.coordinator, entry),))


class WeberConnectionEntity(WeberEntity, BinarySensorEntity):
    """Whether Home Assistant currently receives usable hub data."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WeberCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "connected")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("connected"))

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {
            "source": self.coordinator.data.get("source"),
            "last_data_received": self.coordinator.last_successful_update,
            "last_error": self.coordinator.last_error,
        }

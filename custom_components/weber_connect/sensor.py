"""Native sensor entities for Weber Connect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import WeberCoordinator
from .entity import WeberEntity, build_entity_unique_id
from .models import WeberRuntimeData


@dataclass(frozen=True, kw_only=True)
class WeberSensorDescription(SensorEntityDescription):
    """Describe a value in the coordinator's normalized state."""

    value_fn: Callable[[dict[str, Any]], Any]


def _value(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda data: data.get(key)


SENSORS: tuple[WeberSensorDescription, ...] = (
    *tuple(
        WeberSensorDescription(
            key=f"probe_{number}_temperature",
            translation_key="probe_temperature",
            translation_placeholders={"number": str(number)},
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            value_fn=_value(f"probe_{number}_temperature"),
        )
        for number in range(1, 5)
    ),
)

OBSOLETE_SENSOR_ENTITY_KEYS: tuple[str, ...] = (
    *(f"probe_{number}_{suffix}" for number in range(1, 5) for suffix in ("status", "battery")),
    *(f"cavity_{number}_temperature" for number in range(1, 3)),
    "active_recipe",
    "recipe_state",
    "current_instruction",
    "cook_mode",
    "cook_target_temperature",
    "cook_time_remaining",
    "cook_time_elapsed",
    *(f"timer_{number}_remaining" for number in range(1, 5)),
    "connection_source",
    "app_access",
)
OBSOLETE_ENTITY_KEYS_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "sensor": OBSOLETE_SENSOR_ENTITY_KEYS,
    "binary_sensor": ("connected",),
    "button": (
        "confirm_cook",
        "stop_cook",
        *(f"reset_timer_{number}" for number in range(1, 5)),
    ),
}


def _remove_obsolete_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove entities from pre-release builds that exposed more than four slots."""

    registry = er.async_get(hass)
    identity = entry.unique_id or entry.entry_id
    for platform, keys in OBSOLETE_ENTITY_KEYS_BY_PLATFORM.items():
        for key in keys:
            entity_id = registry.async_get_entity_id(
                platform,
                DOMAIN,
                build_entity_unique_id(identity, key),
            )
            if entity_id is not None:
                registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    _remove_obsolete_entities(hass, entry)
    runtime: WeberRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    async_add_entities(WeberSensor(coordinator, entry, description) for description in SENSORS)


class WeberSensor(WeberEntity, SensorEntity):
    """One permanent Weber probe temperature slot."""

    entity_description: WeberSensorDescription

    def __init__(
        self,
        coordinator: WeberCoordinator,
        entry: ConfigEntry,
        description: WeberSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        key = description.key
        if key.startswith("probe_"):
            number = int(key.split("_")[1])
            nickname = coordinator.options.probe_name(number)
            if nickname:
                description = replace(
                    description,
                    translation_key="probe_temperature_named",
                    translation_placeholders={
                        "nickname": nickname,
                        "number": str(number),
                    },
                )
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        """Show whether a physical probe is currently connected."""

        key = self.entity_description.key
        if key.startswith("probe_") and key.endswith("_temperature"):
            number = key.split("_")[1]
            if self.coordinator.data.get(f"probe_{number}_temperature") is not None:
                return "mdi:thermometer-probe"
            return "mdi:thermometer-probe-off"
        return self.entity_description.icon

    @property
    def available(self) -> bool:
        """Keep permanent probe slots visible while an empty slot is unknown."""

        key = self.entity_description.key
        if key.startswith("probe_") and key.endswith("_temperature"):
            return True
        return super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        number = key.split("_")[1]
        return {
            "probe_number": int(number),
            "probe_state": self.coordinator.data.get(f"probe_{number}_state"),
            "probe_type": self.coordinator.data.get(f"probe_{number}_type"),
            "battery_level": self.coordinator.data.get(f"probe_{number}_battery"),
        }

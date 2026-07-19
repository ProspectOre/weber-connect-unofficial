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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WeberCoordinator
from .entity import WeberEntity
from .models import WeberRuntimeData


@dataclass(frozen=True, kw_only=True)
class WeberSensorDescription(SensorEntityDescription):
    """Describe a value in the coordinator's normalized state."""

    value_fn: Callable[[dict[str, Any]], Any]


def _value(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda data: data.get(key)


def _probe_status(number: int) -> Callable[[dict[str, Any]], str]:
    """Return a plain-language state for a physical probe slot."""

    def value(data: dict[str, Any]) -> str:
        if data.get(f"probe_{number}_temperature") is None:
            return "Not connected"
        raw = str(data.get(f"probe_{number}_state") or "Connected")
        return raw.replace("_", " ").title()

    return value


SENSORS: tuple[WeberSensorDescription, ...] = (
    *tuple(
        WeberSensorDescription(
            key=f"probe_{number}_status",
            translation_key="probe_status",
            translation_placeholders={"number": str(number)},
            icon="mdi:thermometer-probe-off",
            value_fn=_probe_status(number),
        )
        for number in range(1, 5)
    ),
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
    *tuple(
        WeberSensorDescription(
            key=f"probe_{number}_battery",
            translation_key="probe_battery",
            translation_placeholders={"number": str(number)},
            native_unit_of_measurement=PERCENTAGE,
            device_class=SensorDeviceClass.BATTERY,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_value(f"probe_{number}_battery"),
            entity_registry_enabled_default=False,
        )
        for number in range(1, 5)
    ),
    *tuple(
        WeberSensorDescription(
            key=f"cavity_{number}_temperature",
            translation_key="cavity_temperature",
            translation_placeholders={"number": str(number)},
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            value_fn=_value(f"cavity_{number}_temperature"),
            entity_registry_enabled_default=False,
        )
        for number in range(1, 3)
    ),
    WeberSensorDescription(
        key="active_recipe",
        translation_key="active_recipe",
        value_fn=lambda data: data.get("active_recipe") or "No active recipe",
    ),
    WeberSensorDescription(
        key="recipe_state",
        translation_key="recipe_state",
        value_fn=lambda data: data.get("recipe_state") or "Idle",
        entity_registry_enabled_default=False,
    ),
    WeberSensorDescription(
        key="current_instruction",
        translation_key="current_instruction",
        value_fn=lambda data: (
            data.get("current_instruction_short")
            or str(data.get("current_instruction") or "")[:255]
            or "No active instruction"
        ),
    ),
    WeberSensorDescription(
        key="cook_mode",
        translation_key="cook_mode",
        value_fn=lambda data: data.get("cook_mode") or "Not active",
        entity_registry_enabled_default=False,
    ),
    WeberSensorDescription(
        key="cook_target_temperature",
        translation_key="cook_target_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        suggested_display_precision=1,
        value_fn=_value("cook_target_temperature"),
        entity_registry_enabled_default=False,
    ),
    WeberSensorDescription(
        key="cook_time_remaining",
        translation_key="cook_time_remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=_value("cook_time_remaining"),
        entity_registry_enabled_default=False,
    ),
    WeberSensorDescription(
        key="cook_time_elapsed",
        translation_key="cook_time_elapsed",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=_value("cook_time_elapsed"),
        entity_registry_enabled_default=False,
    ),
    *tuple(
        WeberSensorDescription(
            key=f"timer_{number}_remaining",
            translation_key="timer_remaining",
            translation_placeholders={"number": str(number)},
            native_unit_of_measurement=UnitOfTime.SECONDS,
            device_class=SensorDeviceClass.DURATION,
            value_fn=_value(f"timer_{number}_remaining"),
            entity_registry_enabled_default=False,
        )
        for number in range(1, 5)
    ),
    WeberSensorDescription(
        key="connection_source",
        translation_key="connection_source",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            "Weber Cloud"
            if data.get("connected") and data.get("source") == "cloud"
            else "Bluetooth"
            if data.get("connected") and data.get("source") == "bluetooth"
            else "Not receiving data"
        ),
    ),
    WeberSensorDescription(
        key="app_access",
        translation_key="app_access",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            "Available"
            if data.get("cloud_ready")
            else "Paused"
            if data.get("source") == "bluetooth" and data.get("connected")
            else "Setup required"
        ),
    ),
)

PROBE_MEASUREMENT_SENSORS = tuple(
    description
    for description in SENSORS
    if description.key.startswith("probe_")
    and (description.key.endswith("_temperature") or description.key.endswith("_battery"))
)
STATIC_SENSORS = tuple(
    description for description in SENSORS if description not in PROBE_MEASUREMENT_SENSORS
)


def connected_probe_descriptions(
    data: dict[str, Any], already_added: set[str]
) -> list[WeberSensorDescription]:
    """Return new probe entities only for slots that currently contain a probe."""

    connected_numbers = {
        number for number in range(1, 5) if data.get(f"probe_{number}_temperature") is not None
    }
    return [
        description
        for description in PROBE_MEASUREMENT_SENSORS
        if description.key not in already_added
        and int(description.key.split("_")[1]) in connected_numbers
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: WeberRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    added_probe_keys: set[str] = set()

    def add_connected_probes() -> None:
        descriptions = connected_probe_descriptions(coordinator.data, added_probe_keys)
        if not descriptions:
            return
        added_probe_keys.update(description.key for description in descriptions)
        async_add_entities(
            WeberSensor(coordinator, entry, description) for description in descriptions
        )

    async_add_entities(
        WeberSensor(coordinator, entry, description) for description in STATIC_SENSORS
    )
    add_connected_probes()
    entry.async_on_unload(coordinator.async_add_listener(add_connected_probes))


class WeberSensor(WeberEntity, SensorEntity):
    """One native Weber measurement or cook-session field."""

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
                suffix = (
                    "temperature"
                    if key.endswith("_temperature")
                    else "battery"
                    if key.endswith("_battery")
                    else "status"
                )
                description = replace(
                    description,
                    translation_key=f"probe_{suffix}_named",
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
        if key.startswith("probe_") and key.endswith("_status"):
            number = key.split("_")[1]
            if self.coordinator.data.get(f"probe_{number}_temperature") is not None:
                return "mdi:thermometer-probe"
            return "mdi:thermometer-probe-off"
        return self.entity_description.icon

    @property
    def available(self) -> bool:
        """Keep missing numeric measurements out of the active device surface."""

        return bool(super().available and self.native_value is not None)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        if not key.startswith("probe_") or not key.endswith("_temperature"):
            if key == "current_instruction":
                return {
                    "instruction": self.coordinator.data.get("current_instruction"),
                    "instructions": self.coordinator.data.get("instructions", []),
                }
            return None
        number = key.split("_")[1]
        return {
            "probe_number": int(number),
            "probe_state": self.coordinator.data.get(f"probe_{number}_state"),
            "probe_type": self.coordinator.data.get(f"probe_{number}_type"),
        }

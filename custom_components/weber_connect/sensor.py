"""Native sensor entities for Weber Connect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
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


def _timestamp_value(data: dict[str, Any]) -> datetime | None:
    value = data.get("last_successful_update")
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value)


GRILL_TEMPERATURE_SENSOR = WeberSensorDescription(
    key="grill_temperature",
    translation_key="grill_temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
    icon="mdi:grill-outline",
    value_fn=_value("grill_temperature"),
)

SENSORS: tuple[WeberSensorDescription, ...] = (
    GRILL_TEMPERATURE_SENSOR,
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
    WeberSensorDescription(
        key="last_successful_update",
        translation_key="last_successful_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=_timestamp_value,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: WeberRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    async_add_entities(
        WeberSensor(coordinator, entry, description)
        for description in SENSORS
        if description is not GRILL_TEMPERATURE_SENSOR
    )

    grill_sensor_added = False

    def _async_add_grill_sensor() -> None:
        nonlocal grill_sensor_added
        if grill_sensor_added or coordinator.data.get("grill_temperature") is None:
            return
        grill_sensor_added = True
        async_add_entities([WeberSensor(coordinator, entry, GRILL_TEMPERATURE_SENSOR)])

    _async_add_grill_sensor()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_grill_sensor))


class WeberSensor(WeberEntity, SensorEntity):
    """One Weber temperature or update sensor."""

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
        """Keep permanent temperature slots visible while their values are unknown."""

        key = self.entity_description.key
        if key == "grill_temperature":
            return True
        if key.startswith("probe_") and key.endswith("_temperature"):
            return True
        if key == "last_successful_update":
            return True
        return super().available

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        if not key.startswith("probe_"):
            return None
        number = key.split("_")[1]
        return {
            "probe_number": int(number),
            "probe_state": self.coordinator.data.get(f"probe_{number}_state"),
            "probe_type": self.coordinator.data.get(f"probe_{number}_type"),
            "battery_level": self.coordinator.data.get(f"probe_{number}_battery"),
        }

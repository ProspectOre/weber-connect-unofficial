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
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WeberCoordinator
from .entity import WeberEntity, known_probe_numbers
from .models import WeberRuntimeData
from .state import ACTIVE_SESSION_STATES


@dataclass(frozen=True, kw_only=True)
class WeberSensorDescription(SensorEntityDescription):
    """Describe a value in the coordinator's normalized state."""

    value_fn: Callable[[dict[str, Any]], Any]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _value(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda data: data.get(key)


def _attributes(**attributes: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda data: {name: data.get(key) for name, key in attributes.items()}


def _timestamp_value(data: dict[str, Any]) -> datetime | None:
    value = data.get("last_successful_update")
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value)


def _probe_duration_is_meaningful(data: dict[str, Any], number: int) -> bool:
    """Avoid creating a countdown for an idle probe's zero-valued session fields."""

    prefix = f"probe_{number}"
    remaining = data.get(f"{prefix}_time_remaining")
    prompt_remaining = data.get(f"{prefix}_prompt_time_remaining")
    return (
        (isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and remaining > 0)
        or (
            isinstance(prompt_remaining, (int, float))
            and not isinstance(prompt_remaining, bool)
            and prompt_remaining > 0
        )
        or data.get(f"{prefix}_state") in ACTIVE_SESSION_STATES
    )


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

BATTERY_LEVEL_SENSOR = WeberSensorDescription(
    key="battery_level",
    translation_key="battery_level",
    native_unit_of_measurement=PERCENTAGE,
    device_class=SensorDeviceClass.BATTERY,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    suggested_display_precision=0,
    value_fn=_value("battery_level"),
    attributes_fn=_attributes(is_charging="is_charging"),
)

TARGET_GRILL_TEMPERATURE_SENSOR = WeberSensorDescription(
    key="target_grill_temperature",
    translation_key="target_grill_temperature",
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
    value_fn=_value("target_grill_temperature"),
)

WIFI_SIGNAL_SENSOR = WeberSensorDescription(
    key="wifi_signal_strength",
    translation_key="wifi_signal_strength",
    native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    device_class=SensorDeviceClass.SIGNAL_STRENGTH,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=_value("wifi_signal_strength"),
)

FUEL_PERCENT_SENSOR = WeberSensorDescription(
    key="fuel_percent",
    translation_key="fuel_percent",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
    icon="mdi:propane-tank-outline",
    value_fn=_value("fuel_percent"),
)

COOK_INTENSITY_SENSOR = WeberSensorDescription(
    key="cook_intensity",
    translation_key="cook_intensity",
    suggested_display_precision=0,
    icon="mdi:fire",
    value_fn=_value("cook_intensity"),
)

COOK_MODE_OPTIONS = [
    "unknown",
    "grill",
    "smoke_boost",
    "preheat",
    "indirect",
    "custom",
    "simple",
    "manual",
    "sear",
    "steam",
    "warm",
]
WIFI_STATUS_OPTIONS = [
    "unknown",
    "connecting",
    "connected",
    "network_not_found",
    "invalid_password",
    "unsupported_network_type",
    "timed_out",
    "disabled",
    "invalid_password_format",
]
CLOUD_STATUS_OPTIONS = ["unknown", "disconnected", "connecting", "connected"]
DEVICE_STATE_OPTIONS = ["unknown", "idle", "active", "shutting_down", "off"]
FUEL_LEVEL_OPTIONS = [
    "unknown",
    "full",
    "full_to_half",
    "half_to_quarter",
    "quarter_to_low",
    "low",
]
BURNER_STATE_OPTIONS = ["unknown", "off", "on", "ignition_requested"]


def _enum_sensor(
    key: str,
    translation_key: str,
    options: list[str],
    *,
    entity_category: EntityCategory | None = None,
    icon: str | None = None,
) -> WeberSensorDescription:
    return WeberSensorDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.ENUM,
        options=options,
        entity_category=entity_category,
        icon=icon,
        value_fn=_value(key),
    )


COOK_MODE_SENSOR = _enum_sensor("cook_mode", "cook_mode", COOK_MODE_OPTIONS, icon="mdi:chef-hat")
WIFI_STATUS_SENSOR = _enum_sensor(
    "wifi_connection_status",
    "wifi_connection_status",
    WIFI_STATUS_OPTIONS,
    entity_category=EntityCategory.DIAGNOSTIC,
    icon="mdi:wifi",
)
CLOUD_STATUS_SENSOR = _enum_sensor(
    "cloud_connection_status",
    "cloud_connection_status",
    CLOUD_STATUS_OPTIONS,
    entity_category=EntityCategory.DIAGNOSTIC,
    icon="mdi:cloud-outline",
)
DEVICE_STATE_SENSOR = _enum_sensor(
    "device_state",
    "device_state",
    DEVICE_STATE_OPTIONS,
    entity_category=EntityCategory.DIAGNOSTIC,
)
FUEL_LEVEL_SENSOR = _enum_sensor(
    "fuel_level",
    "fuel_level",
    FUEL_LEVEL_OPTIONS,
    icon="mdi:propane-tank-outline",
)

DYNAMIC_HUB_SENSORS = (
    BATTERY_LEVEL_SENSOR,
    GRILL_TEMPERATURE_SENSOR,
    TARGET_GRILL_TEMPERATURE_SENSOR,
    WIFI_SIGNAL_SENSOR,
    WIFI_STATUS_SENSOR,
    CLOUD_STATUS_SENSOR,
    DEVICE_STATE_SENSOR,
    FUEL_PERCENT_SENSOR,
    FUEL_LEVEL_SENSOR,
    COOK_MODE_SENSOR,
    COOK_INTENSITY_SENSOR,
)

SENSORS: tuple[WeberSensorDescription, ...] = (
    *DYNAMIC_HUB_SENSORS,
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

READING_STATUS_OPTIONS = [
    "receiving",
    "reading",
    "waiting",
    "reconnecting",
    "connection_lost",
    "no_reading",
    "device_off",
]
READING_STATUS_SENSORS = (
    _enum_sensor(
        "reading_status", "reading_status", READING_STATUS_OPTIONS, icon="mdi:information-outline"
    ),
    *(
        replace(
            _enum_sensor(
                f"probe_{number}_reading_status",
                "probe_reading_status",
                READING_STATUS_OPTIONS,
                icon="mdi:thermometer-probe",
            ),
            translation_placeholders={"number": str(number)},
        )
        for number in range(1, 5)
    ),
)

BASE_PROBE_NUMBERS = (1, 2)
OPTIONAL_PROBE_NUMBERS = (3, 4)


def _duration_description(
    key: str,
    translation_key: str,
    placeholders: dict[str, str],
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> WeberSensorDescription:
    return WeberSensorDescription(
        key=key,
        translation_key=translation_key,
        translation_placeholders=placeholders,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:timer-outline",
        value_fn=_value(key),
        attributes_fn=attributes_fn,
    )


def _probe_dynamic_descriptions(number: int) -> tuple[WeberSensorDescription, ...]:
    prefix = f"probe_{number}"
    placeholders = {"number": str(number)}
    return (
        WeberSensorDescription(
            key=f"{prefix}_battery",
            translation_key="probe_battery",
            translation_placeholders=placeholders,
            native_unit_of_measurement=PERCENTAGE,
            device_class=SensorDeviceClass.BATTERY,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            suggested_display_precision=0,
            value_fn=_value(f"{prefix}_battery"),
            attributes_fn=_attributes(
                probe_type=f"{prefix}_type",
                serial_number=f"{prefix}_serial_number",
                sku=f"{prefix}_sku",
            ),
        ),
        WeberSensorDescription(
            key=f"{prefix}_ambient_temperature",
            translation_key="probe_ambient_temperature",
            translation_placeholders=placeholders,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            value_fn=_value(f"{prefix}_ambient_temperature"),
        ),
        WeberSensorDescription(
            key=f"{prefix}_case_temperature",
            translation_key="probe_case_temperature",
            translation_placeholders=placeholders,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
            value_fn=_value(f"{prefix}_case_temperature"),
        ),
        _duration_description(
            f"{prefix}_time_remaining",
            "probe_time_remaining",
            placeholders,
            _attributes(
                time_elapsed=f"{prefix}_time_elapsed",
                prompt_time_remaining=f"{prefix}_prompt_time_remaining",
                prompt_time_elapsed=f"{prefix}_prompt_time_elapsed",
                session_state=f"{prefix}_state",
            ),
        ),
    )


def _timed_session_description(number: int) -> WeberSensorDescription:
    prefix = f"timed_session_{number}"
    return _duration_description(
        f"{prefix}_time_remaining",
        "timed_session_time_remaining",
        {"number": str(number)},
        _attributes(
            time_elapsed=f"{prefix}_time_elapsed",
            session_state=f"{prefix}_state",
        ),
    )


def _timer_description(number: int) -> WeberSensorDescription:
    prefix = f"timer_{number}"
    return _duration_description(
        f"{prefix}_time_remaining",
        "timer_time_remaining",
        {"number": str(number)},
        _attributes(
            time_elapsed=f"{prefix}_time_elapsed",
            session_state=f"{prefix}_state",
        ),
    )


def _burner_description(number: int) -> WeberSensorDescription:
    prefix = f"burner_{number}"
    description = _enum_sensor(
        f"{prefix}_state",
        "burner_state",
        BURNER_STATE_OPTIONS,
        icon="mdi:fire-circle",
    )
    return replace(
        description,
        translation_placeholders={"number": str(number)},
        attributes_fn=_attributes(
            burner_type=f"{prefix}_type",
            target_intensity=f"{prefix}_target_intensity",
            actual_intensity=f"{prefix}_actual_intensity",
            flame_sensed=f"{prefix}_flame_sensed",
            locked=f"{prefix}_locked",
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
        for description in (*SENSORS, *READING_STATUS_SENSORS[:3])
        if description.key == "last_successful_update"
        or description.key.endswith("reading_status")
        or description.key in {f"probe_{number}_temperature" for number in BASE_PROBE_NUMBERS}
    )

    known_numbers = known_probe_numbers(hass, entry)
    added_dynamic_sensor_keys: set[str] = set()

    def _async_add_dynamic_sensors() -> None:
        descriptions: list[WeberSensorDescription] = []
        for description in DYNAMIC_HUB_SENSORS:
            if (
                description.key not in added_dynamic_sensor_keys
                and coordinator.data.get(description.key) is not None
            ):
                descriptions.append(description)

        reported_probe_numbers = coordinator.data.get("reported_probe_numbers", ())
        if not isinstance(reported_probe_numbers, (list, tuple)):
            reported_probe_numbers = ()
        for number in OPTIONAL_PROBE_NUMBERS:
            key = f"probe_{number}_temperature"
            if key in added_dynamic_sensor_keys or (
                number not in reported_probe_numbers and number not in known_numbers
            ):
                continue
            descriptions.append(next(row for row in SENSORS if row.key == key))
            descriptions.append(READING_STATUS_SENSORS[number])

        for number in reported_probe_numbers:
            if type(number) is not int or number not in range(1, 5):
                continue
            for description in _probe_dynamic_descriptions(number):
                if description.key == f"probe_{number}_time_remaining" and not (
                    _probe_duration_is_meaningful(coordinator.data, number)
                ):
                    continue
                if (
                    description.key not in added_dynamic_sensor_keys
                    and coordinator.data.get(description.key) is not None
                ):
                    descriptions.append(description)

        reported_timed_session_numbers = coordinator.data.get("reported_timed_session_numbers", ())
        if isinstance(reported_timed_session_numbers, (list, tuple)):
            for number in reported_timed_session_numbers:
                if type(number) is not int:
                    continue
                description = _timed_session_description(number)
                if (
                    description.key not in added_dynamic_sensor_keys
                    and coordinator.data.get(description.key) is not None
                ):
                    descriptions.append(description)

        reported_timer_numbers = coordinator.data.get("reported_timer_numbers", ())
        if isinstance(reported_timer_numbers, (list, tuple)):
            for number in reported_timer_numbers:
                if type(number) is not int:
                    continue
                description = _timer_description(number)
                if (
                    description.key not in added_dynamic_sensor_keys
                    and coordinator.data.get(description.key) is not None
                ):
                    descriptions.append(description)

        reported_burner_numbers = coordinator.data.get("reported_burner_numbers", ())
        if isinstance(reported_burner_numbers, (list, tuple)):
            for number in reported_burner_numbers:
                if type(number) is not int:
                    continue
                description = _burner_description(number)
                if (
                    description.key not in added_dynamic_sensor_keys
                    and coordinator.data.get(description.key) is not None
                ):
                    descriptions.append(description)

        if not descriptions:
            return
        added_dynamic_sensor_keys.update(description.key for description in descriptions)
        async_add_entities(
            WeberSensor(coordinator, entry, description) for description in descriptions
        )

    _async_add_dynamic_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_dynamic_sensors))


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
        if key.startswith("probe_") and key.count("_") == 2 and key.endswith("_temperature"):
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
        attributes = self._telemetry_attributes()
        data = self.coordinator.data
        if "reading_status" not in data:
            return attributes
        key = self.entity_description.key
        status = data.get("reading_status")
        if key.startswith("probe_"):
            status = data.get(f"probe_{key.split('_')[1]}_reading_status", status)
        context = {**(attributes or {}), "reading_status": status}
        # The dedicated timestamp entity tracks live updates. Attach the fixed
        # last-received time during interruptions without recording unchanged
        # battery/temperature values again on every ten-second update.
        if data.get("reading_status") != "receiving":
            context["last_successful_update"] = data.get("last_successful_update")
        return context

    def _telemetry_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        attributes_fn = self.entity_description.attributes_fn
        if attributes_fn is not None:
            return attributes_fn(self.coordinator.data)
        if not key.startswith("probe_"):
            return None
        number = key.split("_")[1]
        return {
            "probe_number": int(number),
            "probe_state": self.coordinator.data.get(f"probe_{number}_state"),
            "probe_type": self.coordinator.data.get(f"probe_{number}_type"),
            "battery_level": self.coordinator.data.get(f"probe_{number}_battery"),
        }

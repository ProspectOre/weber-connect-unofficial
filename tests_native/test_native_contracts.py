"""Product contracts for the clean-slate 3.0 native integration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.weber_connect.binary_sensor import (
    WeberChargingBinarySensor,
    WeberConnectionBinarySensor,
    WeberCookingBinarySensor,
)
from custom_components.weber_connect.binary_sensor import (
    async_setup_entry as async_setup_binary_sensor_entry,
)
from custom_components.weber_connect.bluetooth import generate_identity
from custom_components.weber_connect.config_flow import _is_weber
from custom_components.weber_connect.const import (
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_PROBE_NAME_PREFIX,
    CONF_PROBES,
    DOMAIN,
    NAME,
)
from custom_components.weber_connect.entity import build_entity_unique_id
from custom_components.weber_connect.options import ConnectionMode, WeberOptions
from custom_components.weber_connect.sensor import SENSORS, WeberSensor
from custom_components.weber_connect.sensor import (
    async_setup_entry as async_setup_sensor_entry,
)
from custom_components.weber_connect.state import normalize_state

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_hacs_contract() -> None:
    manifest = json.loads((ROOT / "custom_components" / DOMAIN / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["version"] == "3.1.2"
    assert manifest["config_flow"] is True
    assert manifest["dependencies"] == ["bluetooth_adapters"]
    assert manifest["iot_class"] == "cloud_polling"
    assert {row.get("manufacturer_id") for row in manifest["bluetooth"]} >= {
        0x0DF2,
        0x07C5,
    }
    assert hacs["homeassistant"] == "2026.7.0"
    assert manifest["name"] == NAME == "Weber Connect Unofficial"
    assert hacs["name"] == NAME
    assert manifest["documentation"].endswith("/weber-connect-unofficial")
    assert manifest["issue_tracker"].endswith("/weber-connect-unofficial/issues")


def test_source_strings_match_english_translations() -> None:
    integration = ROOT / "custom_components" / DOMAIN
    strings = json.loads((integration / "strings.json").read_text())
    translations = json.loads((integration / "translations" / "en.json").read_text())
    assert strings == translations
    no_devices = strings["config"]["step"]["no_devices"]["description"]
    for required_guidance in (
        "Initial setup requires a connectable Bluetooth path",
        "Weber Cloud",
        "Fully close the Weber app",
        "turn off Bluetooth",
        "active ESPHome Bluetooth proxy with a free connection slot",
    ):
        assert required_guidance in no_devices


def test_entity_identity_depends_only_on_hub_and_physical_slot() -> None:
    unique_id = build_entity_unique_id("AA:BB:CC:DD:EE:FF", "probe_2_temperature")
    assert unique_id == "AA:BB:CC:DD:EE:FF_probe_2_temperature"


def test_private_identity_has_official_companion_shape() -> None:
    identity = generate_identity()
    assert len(identity.companion_id) == 32
    assert len(identity.public_key) == 128
    int(identity.companion_id + identity.public_key, 16)


def test_weber_discovery_matches_company_ids_and_names() -> None:
    assert _is_weber(SimpleNamespace(manufacturer_data={0x0DF2: b"x"}, name="Hub"))
    assert _is_weber(SimpleNamespace(manufacturer_data={}, name="Weber Connect"))
    assert not _is_weber(SimpleNamespace(manufacturer_data={1: b"x"}, name="Speaker"))


def test_normalized_state_contains_grill_temperature_and_probe_slots() -> None:
    state = normalize_state(
        {
            "actual_cavity_temp_c": 121.5,
            "battery_level": 64,
            "is_charging": True,
            "probes": [
                "invalid",
                {"probe_number": True, "probe_temp_c": 99.0},
                {"probe_number": 5, "probe_temp_c": 99.0},
                {
                    "probe_number": 4,
                    "probe_temp_c": None,
                    "state": "Not connected",
                },
                {
                    "probe_number": 2,
                    "probe_temp_c": 25.4,
                    "battery_level": 87,
                    "state": "CONNECTED",
                    "probe_type": "MEAT",
                },
            ],
            "active_cook": {
                "title": "Private recipe",
                "current_instruction": "Private instruction",
            },
        },
        source="cloud",
        connected=True,
    )
    assert state["probe_1_temperature"] is None
    assert state["probe_2_temperature"] == 25.4
    assert state["probe_2_battery"] == 87
    assert state["reported_probe_numbers"] == (2, 4)
    assert state["grill_temperature"] == 121.5
    assert state["battery_level"] == 64
    assert state["is_charging"] is True
    assert state["source"] == "cloud"
    assert state["connected"] is True
    assert "status" not in state
    assert "active_recipe" not in state
    assert "current_instruction" not in state
    assert "cavity_1_temperature" not in state
    assert "timer_1_remaining" not in state


def test_normalized_state_prefers_actual_cavity_temp_then_display_fallback() -> None:
    fallback_state = normalize_state(
        {
            "actual_cavity_temp_c": None,
            "display_cavity_temp_c": 93.5,
        },
        source="cloud",
        connected=True,
    )
    actual_state = normalize_state(
        {
            "actual_cavity_temp_c": 94.0,
            "display_cavity_temp_c": 93.5,
        },
        source="bluetooth",
        connected=True,
    )

    assert fallback_state["grill_temperature"] == 93.5
    assert actual_state["grill_temperature"] == 94.0


def test_normalized_state_exposes_capability_driven_telemetry() -> None:
    state = normalize_state(
        {
            "target_cavity_temp_c": 135.0,
            "cook_mode": "smoke_boost",
            "simple_intensity": 7,
            "wifi_signal_strength": -61,
            "wifi_connection_status": "connected",
            "cloud_connection_status": "connected",
            "device_state": "active",
            "fuel_percent": 18,
            "fuel_level": "low",
            "software_version": "2.0.3_7398",
            "hardware_version": "rev-b",
            "probes": [
                {
                    "probe_number": 1,
                    "probe_type": "WIRELESS",
                    "state": "ACTIVE",
                    "battery_level": 72,
                    "ambient_temp_c": 38.5,
                    "case_temp_c": 41.0,
                    "time_remaining_s": 900,
                    "time_elapsed_s": 60,
                    "prompt_time_remaining_s": 30,
                    "prompt_time_elapsed_s": 5,
                    "serial_number": "SERIAL",
                    "sku": "SKU",
                }
            ],
            "timed_sessions": [
                {
                    "slot_number": 2,
                    "time_remaining_s": 600,
                    "time_elapsed_s": 120,
                    "state": "ACTIVE",
                }
            ],
            "timers": [
                {
                    "slot_number": 3,
                    "time_remaining_s": 300,
                    "time_elapsed_s": 30,
                    "state": "PAUSED",
                }
            ],
            "burners": [
                {
                    "number": 1,
                    "type": "gas_burner",
                    "state": "on",
                    "target_intensity": 8,
                    "actual_intensity": 6,
                    "flame_sensed": True,
                    "locked": False,
                }
            ],
        },
        source="cloud",
        connected=True,
    )

    assert state["target_grill_temperature"] == 135.0
    assert state["cook_mode"] == "smoke_boost"
    assert state["cook_intensity"] == 7
    assert state["wifi_signal_strength"] == -61
    assert state["fuel_percent"] == 18
    assert state["probe_1_battery"] == 72
    assert state["probe_1_ambient_temperature"] == 38.5
    assert state["probe_1_case_temperature"] == 41.0
    assert state["probe_1_time_remaining"] == 900
    assert state["reported_timed_session_numbers"] == (2,)
    assert state["timed_session_2_time_remaining"] == 600
    assert state["reported_timer_numbers"] == (3,)
    assert state["timer_3_time_remaining"] == 300
    assert state["reported_burner_numbers"] == (1,)
    assert state["burner_1_state"] == "on"
    assert state["burner_1_target_intensity"] == 8
    assert state["burner_1_actual_intensity"] == 6
    assert state["burner_1_flame_sensed"] is True
    assert state["burner_1_locked"] is False
    assert state["cooking"] is True


def test_sensor_surface_supports_grill_up_to_four_probes_and_last_update() -> None:
    descriptions = {description.key: description for description in SENSORS}
    probe_keys = {f"probe_{number}_temperature" for number in range(1, 5)}
    assert set(descriptions) == probe_keys | {
        "battery_level",
        "target_grill_temperature",
        "wifi_signal_strength",
        "wifi_connection_status",
        "cloud_connection_status",
        "device_state",
        "fuel_percent",
        "fuel_level",
        "cook_mode",
        "cook_intensity",
        "grill_temperature",
        "last_successful_update",
    }
    assert len(probe_keys) == 4
    assert all(description.value_fn({}) is None for description in descriptions.values())


@pytest.mark.asyncio
async def test_sensor_platform_adds_grill_probe_and_last_update_entities(hass: object) -> None:
    listeners: list[object] = []
    coordinator = SimpleNamespace(
        data={},
        options=WeberOptions(),
        last_update_success=True,
        async_add_listener=lambda listener: listeners.append(listener) or MagicMock(),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        async_on_unload=MagicMock(),
    )
    batches: list[list[WeberSensor]] = []
    await async_setup_sensor_entry(
        hass,
        entry,
        lambda entities: batches.append(list(entities)),
    )
    assert len(batches) == 1
    assert {entity.entity_description.key for entity in batches[0]} == {
        "probe_1_temperature",
        "probe_2_temperature",
        "last_successful_update",
        "reading_status",
        "probe_1_reading_status",
        "probe_2_reading_status",
    }

    coordinator.data["grill_temperature"] = 121.5
    coordinator.data["battery_level"] = 64
    coordinator.data["is_charging"] = True
    coordinator.data["reported_probe_numbers"] = (1, 2, 4)
    listener = listeners[0]
    assert callable(listener)
    listener()
    listener()

    assert len(batches) == 2
    assert [entity.entity_description.key for entity in batches[1]] == [
        "battery_level",
        "grill_temperature",
        "probe_4_temperature",
        "probe_4_reading_status",
    ]

    coordinator.data["reported_probe_numbers"] = (1, 2, 3, 4)
    listener()
    listener()

    assert len(batches) == 3
    assert [entity.entity_description.key for entity in batches[2]] == [
        "probe_3_temperature",
        "probe_3_reading_status",
    ]


@pytest.mark.asyncio
async def test_sensor_platform_adds_all_reported_dynamic_telemetry(hass: object) -> None:
    coordinator = SimpleNamespace(
        data={
            "battery_level": 64,
            "grill_temperature": 121.5,
            "target_grill_temperature": 135.0,
            "wifi_signal_strength": -61,
            "wifi_connection_status": "connected",
            "cloud_connection_status": "connected",
            "device_state": "active",
            "fuel_percent": 18,
            "fuel_level": "low",
            "cook_mode": "smoke_boost",
            "cook_intensity": 7,
            "reported_probe_numbers": (1,),
            "probe_1_battery": 72,
            "probe_1_type": "WIRELESS",
            "probe_1_serial_number": "SERIAL",
            "probe_1_sku": "SKU",
            "probe_1_ambient_temperature": 38.5,
            "probe_1_case_temperature": 41.0,
            "probe_1_time_remaining": 900,
            "probe_1_time_elapsed": 60,
            "probe_1_prompt_time_remaining": 30,
            "probe_1_prompt_time_elapsed": 5,
            "probe_1_state": "ACTIVE",
            "reported_timed_session_numbers": (2,),
            "timed_session_2_time_remaining": 600,
            "timed_session_2_time_elapsed": 120,
            "timed_session_2_state": "ACTIVE",
            "reported_timer_numbers": (3,),
            "timer_3_time_remaining": 300,
            "timer_3_time_elapsed": 30,
            "timer_3_state": "PAUSED",
            "reported_burner_numbers": (1,),
            "burner_1_state": "on",
            "burner_1_type": "gas_burner",
            "burner_1_target_intensity": 8,
            "burner_1_actual_intensity": 6,
            "burner_1_flame_sensed": True,
            "burner_1_locked": False,
        },
        options=WeberOptions(),
        last_update_success=True,
        async_add_listener=MagicMock(return_value=MagicMock()),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        async_on_unload=MagicMock(),
    )
    batches: list[list[WeberSensor]] = []

    await async_setup_sensor_entry(
        hass,
        entry,
        lambda entities: batches.append(list(entities)),
    )

    dynamic = {entity.entity_description.key: entity for entity in batches[1]}
    assert set(dynamic) == {
        "battery_level",
        "grill_temperature",
        "target_grill_temperature",
        "wifi_signal_strength",
        "wifi_connection_status",
        "cloud_connection_status",
        "device_state",
        "fuel_percent",
        "fuel_level",
        "cook_mode",
        "cook_intensity",
        "probe_1_battery",
        "probe_1_ambient_temperature",
        "probe_1_case_temperature",
        "probe_1_time_remaining",
        "timed_session_2_time_remaining",
        "timer_3_time_remaining",
        "burner_1_state",
    }
    assert dynamic["probe_1_battery"].extra_state_attributes == {
        "probe_type": "WIRELESS",
        "serial_number": "SERIAL",
        "sku": "SKU",
    }
    assert dynamic["probe_1_time_remaining"].extra_state_attributes == {
        "time_elapsed": 60,
        "prompt_time_remaining": 30,
        "prompt_time_elapsed": 5,
        "session_state": "ACTIVE",
    }
    assert dynamic["burner_1_state"].extra_state_attributes == {
        "burner_type": "gas_burner",
        "target_intensity": 8,
        "actual_intensity": 6,
        "flame_sensed": True,
        "locked": False,
    }


@pytest.mark.asyncio
async def test_sensor_platform_ignores_idle_probe_zero_countdown(hass: object) -> None:
    coordinator = SimpleNamespace(
        data={
            "reported_probe_numbers": (1,),
            "probe_1_time_remaining": 0,
            "probe_1_time_elapsed": 2_116_452,
            "probe_1_prompt_time_remaining": 0,
            "probe_1_prompt_time_elapsed": 0,
            "probe_1_state": "PROBED",
        },
        options=WeberOptions(),
        last_update_success=True,
        async_add_listener=MagicMock(return_value=MagicMock()),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        async_on_unload=MagicMock(),
    )
    batches: list[list[WeberSensor]] = []

    await async_setup_sensor_entry(
        hass,
        entry,
        lambda entities: batches.append(list(entities)),
    )

    assert all(
        entity.entity_description.key != "probe_1_time_remaining"
        for batch in batches
        for entity in batch
    )


@pytest.mark.asyncio
async def test_binary_sensor_platform_adds_connection_entity() -> None:
    coordinator = SimpleNamespace(
        data={"connected": True, "source": "cloud"},
        options=WeberOptions(),
        last_update_success=True,
        async_add_listener=MagicMock(return_value=MagicMock()),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        async_on_unload=MagicMock(),
    )
    batches: list[list[WeberConnectionBinarySensor]] = []
    await async_setup_binary_sensor_entry(
        SimpleNamespace(),
        entry,
        lambda entities: batches.append(list(entities)),
    )

    assert len(batches) == 1
    assert len(batches[0]) == 1
    entity = batches[0][0]
    assert entity.is_on is True
    assert entity.available is True
    assert entity.icon == "mdi:cloud-check-outline"
    assert entity.extra_state_attributes == {"connection_method": "Weber Cloud"}

    coordinator.data = {"connected": False, "source": "cloud"}
    assert entity.is_on is False
    assert entity.icon == "mdi:cloud-off-outline"

    coordinator.data = {"connected": True, "source": "bluetooth"}
    assert entity.is_on is True
    assert entity.icon == "mdi:bluetooth-connect"
    assert entity.extra_state_attributes == {"connection_method": "Bluetooth"}


@pytest.mark.asyncio
async def test_binary_sensor_platform_adds_charging_and_cooking_when_reported() -> None:
    listeners: list[object] = []
    coordinator = SimpleNamespace(
        data={
            "connected": True,
            "source": "cloud",
            "battery_level": 64,
            "is_charging": True,
            "cooking": True,
            "cook_mode": "smoke_boost",
        },
        options=WeberOptions(),
        last_update_success=True,
        async_add_listener=lambda listener: listeners.append(listener) or MagicMock(),
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        async_on_unload=MagicMock(),
    )
    batches: list[list[object]] = []

    await async_setup_binary_sensor_entry(
        SimpleNamespace(),
        entry,
        lambda entities: batches.append(list(entities)),
    )

    assert len(batches) == 2
    charging = next(
        entity for entity in batches[1] if isinstance(entity, WeberChargingBinarySensor)
    )
    cooking = next(entity for entity in batches[1] if isinstance(entity, WeberCookingBinarySensor))
    assert charging.is_on is True
    assert charging.extra_state_attributes == {"battery_level": 64}
    assert cooking.is_on is True
    assert cooking.extra_state_attributes == {"cook_mode": "smoke_boost"}

    coordinator.data["is_charging"] = None
    coordinator.data["cooking"] = None
    assert charging.is_on is None
    assert cooking.is_on is None


def test_last_successful_update_is_an_always_visible_timestamp() -> None:
    timestamp = "2026-07-22T15:04:05+00:00"
    coordinator = SimpleNamespace(
        data={"last_successful_update": timestamp},
        options=WeberOptions(),
        last_update_success=False,
    )
    entry = SimpleNamespace(
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    description = next(row for row in SENSORS if row.key == "last_successful_update")
    entity = WeberSensor(coordinator, entry, description)

    assert entity.native_value == datetime.fromisoformat(timestamp)
    assert entity.available is True
    assert entity.extra_state_attributes is None


def test_grill_temperature_is_an_always_visible_measurement() -> None:
    coordinator = SimpleNamespace(
        data={"grill_temperature": 121.5},
        options=WeberOptions(),
        last_update_success=False,
    )
    entry = SimpleNamespace(
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    description = next(row for row in SENSORS if row.key == "grill_temperature")
    entity = WeberSensor(coordinator, entry, description)

    assert entity.native_value == 121.5
    assert entity.available is True
    assert entity.icon == "mdi:grill-outline"
    assert entity.extra_state_attributes is None

    coordinator.data["grill_temperature"] = None
    assert entity.native_value is None
    assert entity.available is True


def test_hub_battery_exposes_charge_level_and_charging_state() -> None:
    coordinator = SimpleNamespace(
        data={"battery_level": 64, "is_charging": True},
        options=WeberOptions(),
        last_update_success=True,
    )
    entry = SimpleNamespace(
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    description = next(row for row in SENSORS if row.key == "battery_level")
    entity = WeberSensor(coordinator, entry, description)

    assert entity.native_value == 64
    assert entity.extra_state_attributes == {"is_charging": True}


def test_invalid_hub_battery_values_normalize_to_unknown() -> None:
    for value in (True, -1, 101, "64"):
        state = normalize_state(
            {"battery_level": value, "is_charging": 1},
            source="cloud",
            connected=True,
        )
        assert state["battery_level"] is None
        assert state["is_charging"] is None


def test_named_probe_preserves_slot_and_single_entity_semantics() -> None:
    coordinator = SimpleNamespace(
        data={
            "probe_2_temperature": 25.0,
            "probe_2_battery": 87,
            "probe_2_state": "CONNECTED",
            "probe_2_type": "MEAT",
        },
        options=WeberOptions(probe_names=("", "Brisket", "", "")),
        last_update_success=True,
    )
    entry = SimpleNamespace(
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    description = next(row for row in SENSORS if row.key == "probe_2_temperature")
    entity = WeberSensor(coordinator, entry, description)

    assert entity.native_value == 25.0
    assert entity.entity_description.translation_key == "probe_temperature_named"
    assert entity.entity_description.translation_placeholders == {
        "nickname": "Brisket",
        "number": "2",
    }
    assert entity.extra_state_attributes == {
        "probe_number": 2,
        "probe_state": "CONNECTED",
        "probe_type": "MEAT",
        "battery_level": 87,
    }
    assert entity.icon == "mdi:thermometer-probe"
    coordinator.data["probe_2_temperature"] = None
    assert entity.native_value is None
    assert entity.available is True
    assert entity.icon == "mdi:thermometer-probe-off"


def test_idle_probe_is_unknown_even_when_transport_is_not_connected() -> None:
    coordinator = SimpleNamespace(
        data={},
        options=WeberOptions(),
        last_update_success=False,
    )
    entry = SimpleNamespace(
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    description = next(row for row in SENSORS if row.key == "probe_1_temperature")
    entity = WeberSensor(coordinator, entry, description)
    assert entity.available is True
    assert entity.native_value is None
    assert entity.icon == "mdi:thermometer-probe-off"


def test_options_have_one_transport_choice_and_stable_probe_names() -> None:
    defaults = WeberOptions.from_mapping({})
    assert defaults.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    assert defaults.cloud_enabled is True
    assert set(defaults.as_dict()) == {CONF_CONNECTION, CONF_PROBES}

    configured = WeberOptions.from_mapping(
        {
            CONF_CONNECTION: {
                CONF_CONNECTION_MODE: "home_assistant_only",
            },
            CONF_PROBES: {f"{CONF_PROBE_NAME_PREFIX}2": " Brisket "},
            "advanced": {"poll_seconds": "120", "local_fallback": True},
        }
    )
    assert configured.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    assert configured.cloud_enabled is True
    assert configured.probe_name(2) == "Brisket"
    assert "advanced" not in configured.as_dict()
    assert configured.as_dict()[CONF_PROBES][f"{CONF_PROBE_NAME_PREFIX}2"] == "Brisket"

    invalid = WeberOptions.from_mapping({CONF_CONNECTION: {CONF_CONNECTION_MODE: "invalid"}})
    assert invalid.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    with pytest.raises(ValueError, match="between 1 and 4"):
        invalid.probe_name(5)


@pytest.mark.parametrize(
    ("raw", "connected", "last_update", "expected"),
    [
        (None, False, None, "waiting"),
        (None, False, "2026-09-04T12:00:00+00:00", "connection_lost"),
        ({"probes": []}, True, None, "no_reading"),
        ({"device_state": "off"}, True, None, "device_off"),
        ({"device_state": "idle"}, True, None, "no_reading"),
        ({"probes": [{"probe_number": 1, "probe_temp_c": 0.0}]}, True, None, "reading"),
    ],
)
def test_reading_status_does_not_infer_unplugged_or_sleeping(
    raw: dict | None, connected: bool, last_update: str | None, expected: str
) -> None:
    state = normalize_state(
        raw, source="cloud", connected=connected, last_successful_update=last_update
    )
    assert state["probe_1_reading_status"] == expected
    assert state["probe_1_state"] is None


def test_retained_sensor_exposes_connection_context_and_last_update() -> None:
    data = normalize_state(
        None, source="cloud", connected=False, last_successful_update="2026-09-04T12:00:00+00:00"
    )
    data["battery_level"] = 64
    coordinator = SimpleNamespace(data=data, options=WeberOptions(), last_update_success=True)
    entry = SimpleNamespace(
        unique_id="hub", entry_id="entry", title="Patio", data={"address": "AA:BB:CC:DD:EE:FF"}
    )
    descriptions = {row.key: row for row in SENSORS}
    for key in ("battery_level", "wifi_connection_status", "probe_1_temperature"):
        sensor = WeberSensor(coordinator, entry, descriptions[key])
        attributes = sensor.extra_state_attributes
        assert attributes["reading_status"] == "connection_lost"
        assert attributes["last_successful_update"] == "2026-09-04T12:00:00+00:00"
    assert WeberSensor(coordinator, entry, descriptions["battery_level"]).native_value == 64


@pytest.mark.parametrize("suffix", ["temperature", "reading_status"])
async def test_known_optional_probe_recovers_after_restart(hass: object, suffix: str) -> None:
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id="hub", data={"address": "AA:BB:CC:DD:EE:FF"})
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"hub_probe_4_{suffix}", config_entry=entry
    )
    listeners = []
    coordinator = SimpleNamespace(
        data=normalize_state(None, source="cloud", connected=False),
        options=WeberOptions(),
        last_update_success=True,
        async_add_listener=lambda listener: listeners.append(listener) or MagicMock(),
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    entities = []
    await async_setup_sensor_entry(hass, entry, lambda batch: entities.extend(batch))
    sensors = {entity.entity_description.key: entity for entity in entities}
    assert "probe_3_reading_status" not in sensors
    assert sensors["probe_4_reading_status"].available
    assert sensors["probe_4_reading_status"].native_value == "waiting"
    assert sensors["probe_4_temperature"].native_value is None
    for state, expected in (({}, "no_reading"), ({"device_state": "off"}, "device_off")):
        coordinator.data = normalize_state(state, source="cloud", connected=True)
        listeners[0]()
        assert sensors["probe_4_reading_status"].native_value == expected
    coordinator.data = normalize_state(
        {"probes": [{"probe_number": 4, "probe_temp_c": 25.0}]}, source="cloud", connected=True
    )
    listeners[0]()
    assert sensors["probe_4_temperature"].native_value == 25.0
    assert sensors["probe_4_reading_status"].native_value == "reading"
    assert len(entities) == len({entity.entity_description.key for entity in entities})

"""Product contracts for the clean-slate 3.0 native integration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    assert manifest["version"] == "3.0.0"
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


def test_entity_identity_depends_only_on_hub_and_physical_slot() -> None:
    unique_id = build_entity_unique_id("AA:BB:CC:DD:EE:FF", "probe_2_temperature")
    assert unique_id == "AA:BB:CC:DD:EE:FF_probe_2_temperature"


def test_private_identity_has_official_companion_shape() -> None:
    identity = generate_identity()
    assert len(identity.companion_id) == 32
    assert len(identity.private_key) == 128
    assert len(identity.public_key) == 128
    int(identity.companion_id + identity.private_key + identity.public_key, 16)


def test_weber_discovery_matches_company_ids_and_names() -> None:
    assert _is_weber(SimpleNamespace(manufacturer_data={0x0DF2: b"x"}, name="Hub"))
    assert _is_weber(SimpleNamespace(manufacturer_data={}, name="Weber Connect"))
    assert not _is_weber(SimpleNamespace(manufacturer_data={1: b"x"}, name="Speaker"))


def test_normalized_state_contains_only_support_metadata_and_probe_slots() -> None:
    state = normalize_state(
        {
            "probes": [
                {
                    "probe_number": 2,
                    "probe_temp_c": 25.4,
                    "battery_level": 87,
                    "state": "CONNECTED",
                    "probe_type": "MEAT",
                }
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
    assert state["source"] == "cloud"
    assert state["connected"] is True
    assert "status" not in state
    assert "active_recipe" not in state
    assert "current_instruction" not in state
    assert "cavity_1_temperature" not in state
    assert "timer_1_remaining" not in state


def test_sensor_surface_contains_exactly_four_permanent_probe_slots() -> None:
    descriptions = {description.key: description for description in SENSORS}
    assert set(descriptions) == {f"probe_{number}_temperature" for number in range(1, 5)}
    assert all(description.value_fn({}) is None for description in descriptions.values())


@pytest.mark.asyncio
async def test_sensor_platform_adds_only_four_entities() -> None:
    coordinator = SimpleNamespace(
        data={},
        options=WeberOptions(),
        last_update_success=True,
    )
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    batches: list[list[WeberSensor]] = []
    await async_setup_sensor_entry(
        SimpleNamespace(),
        entry,
        lambda entities: batches.append(list(entities)),
    )
    assert len(batches) == 1
    assert {entity.entity_description.key for entity in batches[0]} == {
        f"probe_{number}_temperature" for number in range(1, 5)
    }


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
    entity = WeberSensor(coordinator, entry, SENSORS[0])
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
                CONF_CONNECTION_MODE: ConnectionMode.HOME_ASSISTANT_ONLY,
            },
            CONF_PROBES: {f"{CONF_PROBE_NAME_PREFIX}2": " Brisket "},
            "advanced": {"poll_seconds": "120", "local_fallback": True},
        }
    )
    assert configured.cloud_enabled is False
    assert configured.probe_name(2) == "Brisket"
    assert "advanced" not in configured.as_dict()
    assert configured.as_dict()[CONF_PROBES][f"{CONF_PROBE_NAME_PREFIX}2"] == "Brisket"

    invalid = WeberOptions.from_mapping({CONF_CONNECTION: {CONF_CONNECTION_MODE: "invalid"}})
    assert invalid.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    with pytest.raises(ValueError, match="between 1 and 4"):
        invalid.probe_name(5)

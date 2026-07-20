"""Fast contracts for the 3.0 native integration."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from custom_components.weber_connect.bluetooth import generate_identity
from custom_components.weber_connect.config_flow import _is_weber
from custom_components.weber_connect.const import (
    CONF_ADVANCED,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_LOCAL_FALLBACK,
    CONF_POLL_SECONDS,
    CONF_PROBE_NAME_PREFIX,
    CONF_PROBES,
    CONF_REMOTE_CONTROLS,
    DOMAIN,
    NAME,
)
from custom_components.weber_connect.entity import build_entity_unique_id
from custom_components.weber_connect.options import ConnectionMode, WeberOptions
from custom_components.weber_connect.sensor import (
    OBSOLETE_PROBE_ENTITY_KEYS,
    SENSORS,
    WeberSensor,
)
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


def test_native_entity_ids_do_not_reuse_pre_3_registry_state() -> None:
    """3.0 defaults must not be overridden by an old add-on entity registry."""

    unique_id = build_entity_unique_id("AA:BB:CC:DD:EE:FF", "probe_2_temperature")
    assert unique_id == "v3_AA:BB:CC:DD:EE:FF_probe_2_temperature"


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


def test_normalized_state_exposes_full_cook_and_stable_probe_slots() -> None:
    status = {
        "probes": [
            {
                "probe_number": 2,
                "probe_temp_c": 25.4,
                "battery_level": 87,
                "state": "CONNECTED",
                "probe_type": "MEAT",
            }
        ],
        "cavities": [{"cavity_number": 1, "temperature_c": 121.0}],
        "timers": [{"timer_number": 1, "remaining_s": 90}],
        "active_cook": {
            "active": True,
            "title": "Brisket",
            "state": "COOKING",
            "current_instruction": "Keep the lid closed.",
            "current_prompt": {"short_title": "Hold temperature"},
            "current_step": {
                "target_temperature_c": 93.0,
                "cook_mode": "smoke_boost",
            },
            "prompts": [
                {
                    "step_id": 1,
                    "id": 7,
                    "short_title": "Hold temperature",
                    "instruction": "Keep the lid closed.",
                }
            ],
        },
    }
    state = normalize_state(status, source="cloud", connected=True, cloud_ready=True)
    assert state["probe_1_temperature"] is None
    assert state["probe_2_temperature"] == 25.4
    assert state["probe_2_battery"] == 87
    assert state["cavity_1_temperature"] == 121.0
    assert state["timer_1_remaining"] == 90
    assert state["active_recipe"] == "Brisket"
    assert state["current_instruction"] == "Keep the lid closed."
    assert state["instructions"][0]["step_id"] == 1


def test_idle_text_sensors_explain_that_no_cook_is_active() -> None:
    """The device page must not imply an offline integration while idle."""

    descriptions = {description.key: description for description in SENSORS}
    assert descriptions["active_recipe"].value_fn({}) == "No active recipe"
    assert descriptions["current_instruction"].value_fn({}) == "No active instruction"
    assert descriptions["recipe_state"].value_fn({}) == "Idle"
    assert descriptions["cook_mode"].value_fn({}) == "Not active"
    for number in range(1, 5):
        probe = descriptions[f"probe_{number}_temperature"]
        assert probe.value_fn({}) is None


@pytest.mark.asyncio
async def test_sensor_platform_adds_four_permanent_probe_entities() -> None:
    """Every physical slot has one temperature entity, even while empty."""

    class Coordinator:
        def __init__(self) -> None:
            self.data: dict[str, object] = {}
            self.options = WeberOptions.from_mapping({})
            self.last_update_success = True

    coordinator = Coordinator()
    unload_callbacks: list[object] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
        async_on_unload=unload_callbacks.append,
    )
    batches: list[list[WeberSensor]] = []

    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    with patch("custom_components.weber_connect.sensor.er.async_get", return_value=registry):
        await async_setup_sensor_entry(
            SimpleNamespace(), entry, lambda entities: batches.append(list(entities))
        )
    initial_keys = {entity.entity_description.key for entity in batches[0]}
    assert {f"probe_{number}_temperature" for number in range(1, 5)} <= initial_keys
    assert not any(key.endswith("_status") or key.endswith("_battery") for key in initial_keys)
    assert len(batches) == 1
    assert unload_callbacks == []
    assert registry.async_get_entity_id.call_count == len(OBSOLETE_PROBE_ENTITY_KEYS)


@pytest.mark.asyncio
async def test_sensor_platform_removes_redundant_probe_registry_entries() -> None:
    coordinator = SimpleNamespace(data={}, options=WeberOptions.from_mapping({}))
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        unique_id="AA:BB:CC:DD:EE:FF",
        entry_id="entry",
        title="Weber Connect Hub",
        data={"address": "AA:BB:CC:DD:EE:FF"},
    )
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = [
        "sensor.probe_1_status",
        "sensor.probe_1_battery",
        *([None] * (len(OBSOLETE_PROBE_ENTITY_KEYS) - 2)),
    ]

    with patch("custom_components.weber_connect.sensor.er.async_get", return_value=registry):
        await async_setup_sensor_entry(SimpleNamespace(), entry, lambda _entities: None)

    assert registry.async_remove.call_args_list == [
        call("sensor.probe_1_status"),
        call("sensor.probe_1_battery"),
    ]


def test_named_probe_entity_preserves_slot_and_explains_state() -> None:
    """A nickname adds context without hiding the physical probe number."""

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


def test_empty_probe_remains_unknown_when_coordinator_update_fails() -> None:
    """An idle hub must not make permanent probe slots look broken."""

    coordinator = SimpleNamespace(
        data={},
        options=WeberOptions.from_mapping({}),
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


def test_entity_unique_keys_are_complete_and_nonduplicated() -> None:
    keys = [description.key for description in SENSORS]
    assert len(keys) == len(set(keys))
    assert {f"probe_{number}_temperature" for number in range(1, 5)} <= set(keys)
    assert {
        "active_recipe",
        "current_instruction",
        "cook_target_temperature",
        "connection_source",
    } <= set(keys)


def test_options_have_simple_recommended_defaults_and_stable_probe_names() -> None:
    defaults = WeberOptions.from_mapping({})
    assert defaults.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    assert defaults.cloud_enabled is True
    assert defaults.poll_seconds == 10
    assert defaults.local_fallback is False
    assert defaults.remote_controls is False

    configured = WeberOptions.from_mapping(
        {
            CONF_CONNECTION: {
                CONF_CONNECTION_MODE: ConnectionMode.HOME_ASSISTANT_ONLY,
                CONF_REMOTE_CONTROLS: True,
            },
            CONF_PROBES: {f"{CONF_PROBE_NAME_PREFIX}2": " Brisket "},
            CONF_ADVANCED: {
                CONF_POLL_SECONDS: "30",
                CONF_LOCAL_FALLBACK: True,
            },
        }
    )
    assert configured.cloud_enabled is False
    assert configured.remote_controls is True
    assert configured.poll_seconds == 30
    assert configured.local_fallback is True
    assert configured.probe_name(2) == "Brisket"
    assert configured.as_dict()[CONF_PROBES][f"{CONF_PROBE_NAME_PREFIX}2"] == "Brisket"

    invalid = WeberOptions.from_mapping(
        {
            CONF_CONNECTION: {CONF_CONNECTION_MODE: "invalid"},
            CONF_ADVANCED: {CONF_POLL_SECONDS: "invalid"},
        }
    )
    assert invalid.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    assert invalid.poll_seconds == 10
    with pytest.raises(ValueError, match="between 1 and 4"):
        invalid.probe_name(5)


def test_default_device_surface_is_focused() -> None:
    enabled = {
        description.key for description in SENSORS if description.entity_registry_enabled_default
    }
    assert enabled == {
        "probe_1_temperature",
        "probe_2_temperature",
        "probe_3_temperature",
        "probe_4_temperature",
        "active_recipe",
        "current_instruction",
        "app_access",
    }

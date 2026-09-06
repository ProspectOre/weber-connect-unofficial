"""Regression coverage for malformed and changing optional telemetry."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.weber_connect.options import WeberOptions
from custom_components.weber_connect.sensor import WeberSensor, async_setup_entry
from custom_components.weber_connect.state import normalize_state


@pytest.mark.parametrize(
    ("collection", "number_key", "reported_key", "prefix", "value_key", "normalized_key"),
    [
        (
            "timed_sessions",
            "slot_number",
            "reported_timed_session_numbers",
            "timed_session",
            "time_remaining_s",
            "time_remaining",
        ),
        (
            "timers",
            "slot_number",
            "reported_timer_numbers",
            "timer",
            "time_remaining_s",
            "time_remaining",
        ),
        ("burners", "number", "reported_burner_numbers", "burner", "state", "state"),
    ],
)
def test_normalization_rejects_invalid_slots_and_preserves_first_report(
    collection, number_key, reported_key, prefix, value_key, normalized_key
):
    """Bad rows and boolean/out-of-range slots cannot become appliance entities."""
    state = normalize_state(
        {
            collection: [
                None,
                "bad",
                {},
                {number_key: True},
                {number_key: 0},
                {number_key: 17},
                {number_key: "1"},
                {number_key: 1, value_key: "first"},
                {number_key: 1, value_key: "duplicate"},
            ]
        },
        source="cloud",
        connected=True,
    )
    assert state[reported_key] == (1,)
    assert state[f"{prefix}_1_{normalized_key}"] == "first"
    assert not any(key.startswith(f"{prefix}_17_") for key in state)


async def test_optional_sensor_discovery_survives_malformed_reports_and_deduplicates(hass):
    listeners = []
    coordinator = SimpleNamespace(
        data={
            "reported_probe_numbers": "invalid",
            "reported_timed_session_numbers": None,
            "reported_timer_numbers": {},
            "reported_burner_numbers": False,
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
    entities: list[WeberSensor] = []
    await async_setup_entry(hass, entry, lambda batch: entities.extend(batch))
    assert len(entities) == 6
    coordinator.data.update(
        reported_probe_numbers=(None, True, "1", 0, 5, 1),
        reported_timed_session_numbers=(None, True, "1", 1, 2),
        reported_timer_numbers=(None, True, "1", 1, 2),
        reported_burner_numbers=(None, True, "1", 1, 2),
        timed_session_1_time_remaining=120,
        timer_1_time_remaining=30,
        burner_1_state="on",
        reading_status="receiving",
    )
    listeners[0]()
    keys = [entity.entity_description.key for entity in entities]
    assert set(keys[6:]) == {
        "timed_session_1_time_remaining",
        "timer_1_time_remaining",
        "burner_1_state",
    }
    assert entities[-1].extra_state_attributes["reading_status"] == "receiving"
    assert "last_successful_update" not in entities[-1].extra_state_attributes
    listeners[0]()
    assert len(entities) == 9
    coordinator.data.update(
        timed_session_2_time_remaining=0, timer_2_time_remaining=0, burner_2_state="off"
    )
    listeners[0]()
    assert len(entities) == 12
    assert entities[-1].native_value == "off"

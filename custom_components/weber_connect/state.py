"""Normalize local and cloud Weber status into one entity-friendly model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ACTIVE_SESSION_STATES = {"PRIMED", "READY", "ACTIVE", "PAUSED", "ACTIVE_FIXED", "PREHEAT"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percent(value: Any) -> int | None:
    if type(value) is int and 0 <= value <= 100:
        return value
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _intensity(value: Any) -> int | None:
    if type(value) is int and 1 <= value <= 10:
        return value
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def normalize_state(
    status: dict[str, Any] | None,
    *,
    source: str,
    connected: bool,
    last_successful_update: str | None = None,
) -> dict[str, Any]:
    """Return capability-driven appliance telemetry in an entity-friendly shape."""

    raw = status or {}

    # Fall back to display temp if actual temp isn't broadcast
    grill_temp = raw.get("actual_cavity_temp_c")
    if grill_temp is None:
        grill_temp = raw.get("display_cavity_temp_c")

    state: dict[str, Any] = {
        "updated_at": _utc_now(),
        "connected": connected,
        "reading_status": "receiving"
        if connected
        else ("connection_lost" if last_successful_update else "waiting"),
        "source": source,
        "last_successful_update": last_successful_update,
        "grill_temperature": grill_temp,
        "target_grill_temperature": _number(raw.get("target_cavity_temp_c")),
        "cook_mode": _text(raw.get("cook_mode")),
        "cook_intensity": _intensity(raw.get("simple_intensity")),
        "battery_level": _percent(raw.get("battery_level")),
        "is_charging": raw.get("is_charging") if type(raw.get("is_charging")) is bool else None,
        "wifi_signal_strength": (
            raw.get("wifi_signal_strength")
            if type(raw.get("wifi_signal_strength")) is int
            else None
        ),
        "wifi_connection_status": _text(raw.get("wifi_connection_status")),
        "cloud_connection_status": _text(raw.get("cloud_connection_status")),
        "device_state": _text(raw.get("device_state")),
        "fuel_percent": _percent(raw.get("fuel_percent")),
        "fuel_level": _text(raw.get("fuel_level")),
        "software_version": _text(raw.get("software_version")),
        "hardware_version": _text(raw.get("hardware_version")),
    }
    probes = raw.get("probes")
    if not isinstance(probes, list):
        probes = []
    probes_by_number: dict[int, dict[str, Any]] = {}
    for row in probes:
        if not isinstance(row, dict):
            continue
        number = row.get("probe_number")
        if type(number) is int and 1 <= number <= 4:
            probes_by_number.setdefault(number, row)
    state["reported_probe_numbers"] = tuple(sorted(probes_by_number))
    for number in range(1, 5):
        probe = probes_by_number.get(number, {})
        state[f"probe_{number}_temperature"] = probe.get("probe_temp_c")
        state[f"probe_{number}_ambient_temperature"] = probe.get("ambient_temp_c")
        state[f"probe_{number}_case_temperature"] = probe.get("case_temp_c")
        state[f"probe_{number}_battery"] = _percent(probe.get("battery_level"))
        state[f"probe_{number}_time_remaining"] = probe.get("time_remaining_s")
        state[f"probe_{number}_time_elapsed"] = probe.get("time_elapsed_s")
        state[f"probe_{number}_prompt_time_remaining"] = probe.get("prompt_time_remaining_s")
        state[f"probe_{number}_prompt_time_elapsed"] = probe.get("prompt_time_elapsed_s")
        state[f"probe_{number}_state"] = probe.get("state")
        state[f"probe_{number}_reading_status"] = (
            state["reading_status"]
            if not connected
            else "reading"
            if probe.get("probe_temp_c") is not None
            else "device_off"
            if raw.get("device_state") == "off"
            else "no_reading"
        )
        state[f"probe_{number}_type"] = probe.get("probe_type")
        state[f"probe_{number}_serial_number"] = probe.get("serial_number")
        state[f"probe_{number}_sku"] = probe.get("sku")

    timed_sessions = raw.get("timed_sessions")
    if not isinstance(timed_sessions, list):
        timed_sessions = []
    timed_by_number: dict[int, dict[str, Any]] = {}
    for row in timed_sessions:
        if not isinstance(row, dict):
            continue
        number = row.get("slot_number")
        if type(number) is int and 1 <= number <= 16:
            timed_by_number.setdefault(number, row)
    state["reported_timed_session_numbers"] = tuple(sorted(timed_by_number))
    for number, row in timed_by_number.items():
        state[f"timed_session_{number}_time_remaining"] = row.get("time_remaining_s")
        state[f"timed_session_{number}_time_elapsed"] = row.get("time_elapsed_s")
        state[f"timed_session_{number}_state"] = row.get("state")

    timers = raw.get("timers")
    if not isinstance(timers, list):
        timers = []
    timers_by_number: dict[int, dict[str, Any]] = {}
    for row in timers:
        if not isinstance(row, dict):
            continue
        number = row.get("slot_number")
        if type(number) is int and 1 <= number <= 16:
            timers_by_number.setdefault(number, row)
    state["reported_timer_numbers"] = tuple(sorted(timers_by_number))
    for number, row in timers_by_number.items():
        state[f"timer_{number}_time_remaining"] = row.get("time_remaining_s")
        state[f"timer_{number}_time_elapsed"] = row.get("time_elapsed_s")
        state[f"timer_{number}_state"] = row.get("state")

    burners = raw.get("burners")
    if not isinstance(burners, list):
        burners = []
    burners_by_number: dict[int, dict[str, Any]] = {}
    for row in burners:
        if not isinstance(row, dict):
            continue
        number = row.get("number")
        if type(number) is int and 1 <= number <= 16:
            burners_by_number.setdefault(number, row)
    state["reported_burner_numbers"] = tuple(sorted(burners_by_number))
    for number, row in burners_by_number.items():
        state[f"burner_{number}_state"] = row.get("state")
        state[f"burner_{number}_type"] = row.get("type")
        state[f"burner_{number}_target_intensity"] = _intensity(row.get("target_intensity"))
        state[f"burner_{number}_actual_intensity"] = _intensity(row.get("actual_intensity"))
        state[f"burner_{number}_flame_sensed"] = (
            row.get("flame_sensed") if type(row.get("flame_sensed")) is bool else None
        )
        state[f"burner_{number}_locked"] = (
            row.get("locked") if type(row.get("locked")) is bool else None
        )

    all_session_states = [
        row.get("state")
        for row in [
            *probes_by_number.values(),
            *timed_by_number.values(),
            *timers_by_number.values(),
        ]
    ]
    burner_states = [row.get("state") for row in burners_by_number.values()]
    activity_reported = bool(all_session_states or burner_states or state["cook_mode"] is not None)
    state["cooking"] = (
        any(value in ACTIVE_SESSION_STATES for value in all_session_states)
        or any(value in {"on", "ignition_requested"} for value in burner_states)
        if activity_reported
        else None
    )
    return state

"""Normalize local and cloud Weber status into one entity-friendly model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_state(
    status: dict[str, Any] | None,
    *,
    source: str,
    connected: bool,
    cloud_ready: bool,
) -> dict[str, Any]:
    """Return a stable shape even when the hub has no active cook."""

    raw = status or {}
    state: dict[str, Any] = {
        "updated_at": _utc_now(),
        "connected": connected,
        "cloud_ready": cloud_ready,
        "source": source,
        "status": raw,
    }
    probes = raw.get("probes")
    if not isinstance(probes, list):
        probes = []
    for number in range(1, 5):
        probe = next(
            (row for row in probes if isinstance(row, dict) and row.get("probe_number") == number),
            {},
        )
        state[f"probe_{number}_temperature"] = probe.get("probe_temp_c")
        state[f"probe_{number}_battery"] = probe.get("battery_level")
        state[f"probe_{number}_state"] = probe.get("state") or "Not connected"
        state[f"probe_{number}_type"] = probe.get("probe_type")

    cavities = raw.get("cavities")
    if not isinstance(cavities, list):
        cavities = []
    for number in range(1, 3):
        cavity = next(
            (
                row
                for row in cavities
                if isinstance(row, dict) and row.get("cavity_number") == number
            ),
            {},
        )
        state[f"cavity_{number}_temperature"] = cavity.get("temperature_c")
    if raw.get("actual_cavity_temp_c") is not None:
        state["cavity_1_temperature"] = raw.get("actual_cavity_temp_c")

    timers = raw.get("timers")
    if not isinstance(timers, list):
        timers = []
    for number in range(1, 5):
        timer = next(
            (row for row in timers if isinstance(row, dict) and row.get("timer_number") == number),
            {},
        )
        state[f"timer_{number}_remaining"] = timer.get("remaining_s")

    active_cook = raw.get("active_cook")
    if not isinstance(active_cook, dict):
        active_cook = {}
    current_step = active_cook.get("current_step")
    if not isinstance(current_step, dict):
        current_step = {}
    current_prompt = active_cook.get("current_prompt")
    if not isinstance(current_prompt, dict):
        current_prompt = {}
    state.update(
        {
            "active_cook": active_cook,
            "active_recipe": active_cook.get("title"),
            "recipe_state": active_cook.get("state"),
            "current_instruction": active_cook.get("current_instruction"),
            "current_instruction_short": current_prompt.get("short_title"),
            "cook_target_temperature": current_step.get("target_temperature_c"),
            "cook_time_remaining": active_cook.get("time_remaining_s"),
            "cook_time_elapsed": active_cook.get("time_elapsed_s"),
            "cook_mode": current_step.get("cook_mode") or raw.get("cook_mode"),
            "instructions": [
                {
                    "step_id": prompt.get("step_id"),
                    "prompt_id": prompt.get("id"),
                    "title": prompt.get("short_title"),
                    "instruction": prompt.get("instruction"),
                }
                for prompt in active_cook.get("prompts", [])
                if isinstance(prompt, dict)
            ],
        }
    )
    return state

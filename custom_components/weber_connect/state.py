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
    last_successful_update: str | None = None,
) -> dict[str, Any]:
    """Return model-aware probe data, the grill sensor, and connection context."""

    raw = status or {}
    state: dict[str, Any] = {
        "updated_at": _utc_now(),
        "connected": connected,
        "source": source,
        "last_successful_update": last_successful_update,
        "grill_temperature": raw.get("actual_cavity_temp_c"),
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
        state[f"probe_{number}_battery"] = probe.get("battery_level")
        state[f"probe_{number}_state"] = probe.get("state") or "Not connected"
        state[f"probe_{number}_type"] = probe.get("probe_type")
    return state

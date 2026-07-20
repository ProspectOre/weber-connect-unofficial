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
) -> dict[str, Any]:
    """Return the four stable probe slots and minimal support metadata."""

    raw = status or {}
    state: dict[str, Any] = {
        "updated_at": _utc_now(),
        "connected": connected,
        "source": source,
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
    return state

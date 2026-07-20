"""Privacy-safe diagnostics for Weber Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import (
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
)
from .models import WeberRuntimeData

TO_REDACT = {
    CONF_ADDRESS,
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    "companion_private_key",
    "companion_public_key",
    "appliance_public_key",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return support data with credentials and device identifiers removed."""

    runtime: WeberRuntimeData = entry.runtime_data
    coordinator = runtime.coordinator
    state = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "stored_options": dict(entry.options),
        "effective_options": coordinator.options.as_dict(),
        "transport": coordinator.source,
        "connected": state.get("connected", False),
        "last_successful_update": coordinator.last_successful_update,
        "consecutive_failures": coordinator.consecutive_failures,
        "successful_updates": coordinator.successful_updates,
        "failed_updates": coordinator.failed_updates,
        "last_error": coordinator.last_error,
        "probe_slots": [
            {
                "number": number,
                "temperature_c": state.get(f"probe_{number}_temperature"),
                "state": state.get(f"probe_{number}_state"),
                "type": state.get(f"probe_{number}_type"),
                "battery_level": state.get(f"probe_{number}_battery"),
            }
            for number in range(1, 5)
        ],
        "cloud_socket_received_types": (
            list(coordinator.cloud_session.received_types)
            if coordinator.cloud_session is not None
            else []
        ),
    }

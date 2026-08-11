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
        "grill_temperature_c": state.get("grill_temperature"),
        "target_grill_temperature_c": state.get("target_grill_temperature"),
        "cook_mode": state.get("cook_mode"),
        "cook_intensity": state.get("cook_intensity"),
        "cooking": state.get("cooking"),
        "hub_battery_level": state.get("battery_level"),
        "hub_is_charging": state.get("is_charging"),
        "wifi_signal_strength": state.get("wifi_signal_strength"),
        "wifi_connection_status": state.get("wifi_connection_status"),
        "cloud_connection_status": state.get("cloud_connection_status"),
        "device_state": state.get("device_state"),
        "fuel_percent": state.get("fuel_percent"),
        "fuel_level": state.get("fuel_level"),
        "software_version": state.get("software_version"),
        "hardware_version": state.get("hardware_version"),
        "probe_slots": [
            {
                "number": number,
                "temperature_c": state.get(f"probe_{number}_temperature"),
                "state": state.get(f"probe_{number}_state"),
                "type": state.get(f"probe_{number}_type"),
                "battery_level": state.get(f"probe_{number}_battery"),
                "ambient_temperature_c": state.get(f"probe_{number}_ambient_temperature"),
                "case_temperature_c": state.get(f"probe_{number}_case_temperature"),
                "time_remaining_s": state.get(f"probe_{number}_time_remaining"),
            }
            for number in range(1, 5)
        ],
        "timed_sessions": [
            {
                "number": number,
                "time_remaining_s": state.get(f"timed_session_{number}_time_remaining"),
                "state": state.get(f"timed_session_{number}_state"),
            }
            for number in state.get("reported_timed_session_numbers", ())
        ],
        "timers": [
            {
                "number": number,
                "time_remaining_s": state.get(f"timer_{number}_time_remaining"),
                "state": state.get(f"timer_{number}_state"),
            }
            for number in state.get("reported_timer_numbers", ())
        ],
        "burners": [
            {
                "number": number,
                "state": state.get(f"burner_{number}_state"),
                "type": state.get(f"burner_{number}_type"),
                "target_intensity": state.get(f"burner_{number}_target_intensity"),
                "actual_intensity": state.get(f"burner_{number}_actual_intensity"),
                "flame_sensed": state.get(f"burner_{number}_flame_sensed"),
                "locked": state.get(f"burner_{number}_locked"),
            }
            for number in state.get("reported_burner_numbers", ())
        ],
        "cloud_socket_received_types": (
            list(coordinator.cloud_session.received_types)
            if coordinator.cloud_session is not None
            else []
        ),
    }

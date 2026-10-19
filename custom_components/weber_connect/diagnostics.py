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
    CONF_MANUFACTURER_ID,
    CONF_MANUFACTURER_SCHEMA,
    CONF_MODEL_ID,
    CONF_PAIRING_KEY_REF,
    CONF_TRANSPORT_FAMILY,
    MAX_PROBE_SLOTS,
)
from .entity import known_probe_numbers
from .models import WeberRuntimeData
from .support import cloud_telemetry_metrics, error_category, report_placeholders, support_report

TO_REDACT = {
    CONF_ADDRESS,
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_PAIRING_KEY_REF,
    "companion_private_key",
    "companion_public_key",
    "appliance_public_key",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return support data with credentials and device identifiers removed."""

    report = support_report(stage="device_diagnostics", entry=entry)
    support = {"support_report": report, **report_placeholders(report)}
    runtime: WeberRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        return support
    coordinator = runtime.coordinator
    state = coordinator.data
    probe_numbers = known_probe_numbers(hass, entry) | {
        number
        for number in state.get("reported_probe_numbers", ())
        if type(number) is int and 1 <= number <= MAX_PROBE_SLOTS
    }
    return {
        **support,
        "entry": async_redact_data(
            {key: value for key, value in entry.data.items() if key in TO_REDACT}, TO_REDACT
        ),
        "stored_options": async_redact_data(dict(entry.options), set(entry.options)),
        "effective_options": async_redact_data(coordinator.options.as_dict(), {"probes"}),
        "transport": coordinator.source,
        "bluetooth_protocol": {
            "family": entry.data.get(CONF_TRANSPORT_FAMILY, "saber"),
            "manufacturer_id": entry.data.get(CONF_MANUFACTURER_ID),
            "manufacturer_schema": entry.data.get(CONF_MANUFACTURER_SCHEMA),
            "model_id": entry.data.get(CONF_MODEL_ID),
        },
        "connected": state.get("connected", False),
        "last_successful_update": coordinator.last_successful_update,
        "consecutive_failures": coordinator.consecutive_failures,
        "successful_updates": coordinator.successful_updates,
        "failed_updates": coordinator.failed_updates,
        "last_error": error_category(coordinator.last_error),
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
        "last_reported_cloud_connection_status": state.get("last_reported_cloud_connection_status"),
        "device_state": state.get("device_state"),
        "fuel_percent": state.get("fuel_percent"),
        "fuel_level": state.get("fuel_level"),
        "software_version": state.get("software_version"),
        "hardware_version": state.get("hardware_version"),
        "cloud_telemetry": cloud_telemetry_metrics(coordinator.cloud_session),
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
            for number in sorted(probe_numbers)
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
        "cloud_socket_connections": (
            coordinator.cloud_session.socket_connections
            if coordinator.cloud_session is not None
            else 0
        ),
        "cloud_socket_fast_recoveries": (
            coordinator.cloud_session.fast_recoveries
            if coordinator.cloud_session is not None
            else 0
        ),
        "cloud_socket_last_error_type": (
            coordinator.cloud_session.last_error_type
            if coordinator.cloud_session is not None
            else None
        ),
    }

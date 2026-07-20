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
    CONF_COMPANION_PRIVATE_KEY,
    CONF_COMPANION_PUBLIC_KEY,
)
from .models import WeberRuntimeData

TO_REDACT = {
    CONF_ADDRESS,
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_COMPANION_PRIVATE_KEY,
    CONF_COMPANION_PUBLIC_KEY,
    "appliance_public_key",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return support data with credentials and device identifiers removed."""

    runtime: WeberRuntimeData = entry.runtime_data
    cloud_client = runtime.coordinator.cloud_client
    socket_client = getattr(cloud_client, "_socket_client", None)
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "stored_options": dict(entry.options),
        "effective_options": runtime.coordinator.options.as_dict(),
        "state": async_redact_data(dict(runtime.coordinator.data), TO_REDACT),
        "transport": runtime.coordinator.data.get("source"),
        "poll_seconds": runtime.coordinator.poll_seconds,
        "last_successful_update": runtime.coordinator.last_successful_update,
        "consecutive_failures": runtime.coordinator.consecutive_failures,
        "successful_updates": runtime.coordinator.successful_updates,
        "failed_updates": runtime.coordinator.failed_updates,
        "last_error": runtime.coordinator.last_error,
        "cloud_live_error": (
            getattr(cloud_client, "socket_error", None) if cloud_client is not None else None
        ),
        "cloud_socket_received_types": getattr(socket_client, "received_types", []),
        "cloud_history_schema": (
            {
                "session": getattr(cloud_client, "session_schema", {}),
                "snapshot": getattr(cloud_client, "snapshot_schema", {}),
            }
            if cloud_client is not None
            else {}
        ),
    }

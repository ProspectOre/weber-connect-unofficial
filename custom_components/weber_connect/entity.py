"""Shared entity base for Weber Connect."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CAPABILITIES,
    CONF_MODEL_ID,
    DOMAIN,
    MANUFACTURER,
    MAX_PROBE_SLOTS,
)
from .coordinator import WeberCoordinator

_LEGACY_MODEL_LABELS = {
    1: "Connect Smart Grilling Hub",
    2: "SmokeFire",
    5: "Pulse 1000",
    6: "Pulse 2000",
    7: "Spirit Smart Grill",
    8: "Genesis Smart Grill",
}


def _is_smokefire_title(title: str) -> bool:
    normalized = title.casefold().strip()
    return normalized.startswith("smokefire") and (
        len(normalized) == len("smokefire") or not normalized[len("smokefire")].isalpha()
    )


def build_entity_unique_id(identity: str, key: str) -> str:
    """Build a stable identity from the hub and physical slot."""

    return f"{identity}_{key}"


def known_probe_numbers(hass: HomeAssistant, entry: ConfigEntry) -> set[int]:
    """Keep known physical slots available even before new telemetry arrives."""

    identity = entry.unique_id or entry.entry_id
    registered = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    capabilities = entry.data.get(CONF_CAPABILITIES, {})
    capability_count = capabilities.get("probe_count") if isinstance(capabilities, dict) else None
    if type(capability_count) is int:
        initial = set(range(1, min(max(capability_count, 0), MAX_PROBE_SLOTS) + 1))
    else:
        initial = {1, 2}
    return initial | {
        number
        for number in range(1, MAX_PROBE_SLOTS + 1)
        if any(
            build_entity_unique_id(identity, f"probe_{number}_{suffix}") in registered
            for suffix in ("temperature", "reading_status")
        )
    }


class WeberEntity(CoordinatorEntity[WeberCoordinator]):
    """Base class tying every entity to one stable Weber hub device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WeberCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        identity = entry.unique_id or entry.entry_id
        self._attr_unique_id = build_entity_unique_id(identity, key)
        model_id_value = entry.data.get(CONF_MODEL_ID)
        model_id = model_id_value if type(model_id_value) is int else None
        title = entry.title.strip()
        if model_id in (None, 1, 2) and _is_smokefire_title(title):
            model = "SmokeFire"
        elif model_id in _LEGACY_MODEL_LABELS:
            model = _LEGACY_MODEL_LABELS[model_id]
        else:
            model = "Weber Connect controller"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            connections={(CONNECTION_BLUETOOTH, str(entry.data[CONF_ADDRESS]))},
            manufacturer=MANUFACTURER,
            model=model,
            name=entry.title,
        )

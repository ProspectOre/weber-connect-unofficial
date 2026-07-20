"""Unofficial native Home Assistant integration for Weber Connect."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import WeberCoordinator
from .models import WeberRuntimeData


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Weber hub from a config entry."""

    coordinator = WeberCoordinator(hass, entry)
    # A sleeping grill or a busy Bluetooth proxy must never delay Home
    # Assistant startup. Publish a complete, honest initial state immediately;
    # the entry-scoped background loop performs the first transport read after
    # the entity platforms are ready.
    coordinator.async_set_updated_data(coordinator.initial_state())
    entry.runtime_data = WeberRuntimeData(coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Weber hub and close its cloud connection."""

    runtime: WeberRuntimeData = entry.runtime_data
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await runtime.coordinator.async_close()
    return unloaded

"""Opt-in remote cook controls for Weber Connect."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WeberCoordinator
from .entity import WeberEntity
from .models import WeberRuntimeData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime: WeberRuntimeData = entry.runtime_data
    if not runtime.coordinator.remote_controls:
        return
    async_add_entities(
        (
            WeberSessionButton(runtime.coordinator, entry, "confirm"),
            WeberSessionButton(runtime.coordinator, entry, "stop"),
            *(WeberTimerResetButton(runtime.coordinator, entry, index) for index in range(4)),
        )
    )


class WeberSessionButton(WeberEntity, ButtonEntity):
    """Confirm a prompt or stop a cook already started elsewhere."""

    def __init__(self, coordinator: WeberCoordinator, entry: ConfigEntry, command: str) -> None:
        super().__init__(coordinator, entry, f"{command}_cook")
        self._command = command
        self._attr_translation_key = f"{command}_cook"

    @property
    def available(self) -> bool:
        active = self.coordinator.data.get("active_cook")
        return bool(
            super().available
            and self.coordinator.remote_controls
            and self.coordinator.cloud_ready
            and isinstance(active, dict)
            and active.get("active")
        )

    async def async_press(self) -> None:
        await self.coordinator.async_session_command(self._command)


class WeberTimerResetButton(WeberEntity, ButtonEntity):
    """Reset one timer already present on the hub."""

    def __init__(self, coordinator: WeberCoordinator, entry: ConfigEntry, timer_index: int) -> None:
        number = timer_index + 1
        super().__init__(coordinator, entry, f"reset_timer_{number}")
        self._timer_index = timer_index
        self._attr_translation_key = "reset_timer"
        self._attr_translation_placeholders = {"number": str(number)}
        self._attr_entity_registry_enabled_default = False

    @property
    def available(self) -> bool:
        return bool(
            super().available
            and self.coordinator.remote_controls
            and self.coordinator.cloud_ready
            and self.coordinator.data.get(f"timer_{self._timer_index + 1}_remaining") is not None
        )

    async def async_press(self) -> None:
        await self.coordinator.async_reset_timer(self._timer_index)

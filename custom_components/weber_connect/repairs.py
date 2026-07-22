"""Credential repair for Weber Connect Unofficial."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant


class CredentialRepairFlow(RepairsFlow):
    """Remove a rejected generated companion so it can be paired again."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            if self.hass.config_entries.async_get_entry(self._entry_id) is not None:
                await self.hass.config_entries.async_remove(self._entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a repair flow for a rejected generated credential."""

    entry_id = data.get("entry_id") if data is not None else None
    if not isinstance(entry_id, str):
        raise ValueError(f"Repair {issue_id} is missing its config entry.")
    if not issue_id.startswith("credentials_rejected_"):
        raise ValueError(f"Repair {issue_id} is no longer supported.")
    return CredentialRepairFlow(entry_id)

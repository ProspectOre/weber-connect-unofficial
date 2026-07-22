"""Native Home Assistant credential-repair coverage."""

from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect.const import DOMAIN
from custom_components.weber_connect.repairs import async_create_fix_flow

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.asyncio
async def test_repair_rejects_retired_connection_issue_and_invalid_data(hass: object) -> None:
    with pytest.raises(ValueError, match="no longer supported"):
        await async_create_fix_flow(
            hass,
            "connection_lost_retired",
            {"entry_id": "retired"},
        )
    with pytest.raises(ValueError, match="missing its config entry"):
        await async_create_fix_flow(hass, "credentials_rejected_invalid", None)


@pytest.mark.asyncio
async def test_credential_repair_removes_rejected_entry(hass: object) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="rejected-hub")
    entry.add_to_hass(hass)
    flow = await async_create_fix_flow(
        hass,
        f"credentials_rejected_{entry.entry_id}",
        {"entry_id": entry.entry_id},
    )
    flow.hass = hass

    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM

    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.async_get_entry(entry.entry_id) is None

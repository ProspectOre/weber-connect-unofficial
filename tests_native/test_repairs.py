"""Native Home Assistant repair-flow coverage."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect.const import DOMAIN
from custom_components.weber_connect.models import WeberRuntimeData
from custom_components.weber_connect.repairs import async_create_fix_flow

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.asyncio
async def test_connection_repair_recovers_when_data_resumes(hass: object) -> None:
    coordinator = SimpleNamespace(
        data={"connected": True},
        async_refresh=AsyncMock(),
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="test-hub")
    entry.runtime_data = WeberRuntimeData(coordinator=coordinator)
    entry.add_to_hass(hass)

    flow = await async_create_fix_flow(
        hass,
        f"connection_lost_{entry.entry_id}",
        {"entry_id": entry.entry_id},
    )
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM

    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_repair_stays_open_while_unavailable(hass: object) -> None:
    coordinator = SimpleNamespace(
        data={"connected": False},
        async_refresh=AsyncMock(),
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="test-hub")
    entry.runtime_data = WeberRuntimeData(coordinator=coordinator)
    entry.add_to_hass(hass)
    flow = await async_create_fix_flow(
        hass,
        f"connection_lost_{entry.entry_id}",
        {"entry_id": entry.entry_id},
    )
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "still_unavailable"}


@pytest.mark.asyncio
async def test_connection_repair_handles_removed_entry_and_invalid_data(hass: object) -> None:
    flow = await async_create_fix_flow(
        hass,
        "connection_lost_removed",
        {"entry_id": "removed"},
    )
    flow.hass = hass
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    with pytest.raises(ValueError, match="missing its config entry"):
        await async_create_fix_flow(hass, "connection_lost_invalid", None)

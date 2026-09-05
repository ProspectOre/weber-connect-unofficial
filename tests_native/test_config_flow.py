"""End-to-end native config-flow tests against Home Assistant."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect import async_setup_entry
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.config_flow import WeberConnectConfigFlow
from custom_components.weber_connect.const import (
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_PROBES,
    DOMAIN,
)
from custom_components.weber_connect.models import CompanionIdentity, PairingResult
from custom_components.weber_connect.options import ConnectionMode, WeberOptions
from custom_components.weber_connect.weber_cloud import CloudConfig

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def mock_platform_bluetooth_dependencies() -> Generator[None]:
    """Keep config-flow tests independent of the runner's Bluetooth hardware."""

    with patch(
        "homeassistant.setup._async_process_dependencies",
        new=AsyncMock(return_value=[]),
    ):
        yield


async def _finish_progress(hass: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Advance Home Assistant progress steps until the flow reaches a decision."""

    for _attempt in range(10):
        if result["type"] is not FlowResultType.SHOW_PROGRESS:
            return result
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    raise AssertionError("Config flow did not finish after 10 progress updates")


class FakeCloudClient:
    """Cloud registration double with no network access."""

    association_codes: ClassVar[list[str]] = []
    associated_appliance_id: ClassVar[str | None] = None
    events: ClassVar[list[str]] = []
    timeouts: ClassVar[list[float]] = []

    def __init__(self, config: object, *, timeout: float = 20.0) -> None:
        self.config = config
        self.timeout = timeout
        self.timeouts.append(timeout)
        self.authenticated = False

    def authenticate(self) -> str:
        self.authenticated = True
        self.events.append("cloud_registered")
        return "token"

    def close(self) -> None:
        return None

    def associated_appliances(self) -> list[dict[str, object]]:
        if self.associated_appliance_id is None:
            return []
        return [{"appliance_id": self.associated_appliance_id}]


class EventuallyAssociatedCloudClient(FakeCloudClient):
    """Cloud double that models Weber's delayed association propagation."""

    appliance_id: ClassVar[str] = ""
    checks: ClassVar[int] = 0

    def associated_appliances(self) -> list[dict[str, object]]:
        self.__class__.checks += 1
        if self.checks < 3:
            return []
        return [{"appliance_id": self.appliance_id}]


@pytest.mark.asyncio
async def test_user_flow_creates_private_companion_entry(hass: object) -> None:
    discovery = SimpleNamespace(
        address=ADDRESS,
        name="Weber Connect Hub",
        manufacturer_data={0x0DF2: b"weber"},
    )
    identity = CompanionIdentity(
        companion_id="11" * 16,
        public_key="33" * 64,
    )
    pairing = PairingResult(
        message_version=10,
        appliance_id="44" * 16,
    )
    pairing_started = asyncio.Event()
    allow_pairing = asyncio.Event()

    async def delayed_pairing(*_args: object, **_kwargs: object) -> PairingResult:
        FakeCloudClient.events.append("bluetooth_pairing")
        pairing_started.set()
        await allow_pairing.wait()
        FakeCloudClient.associated_appliance_id = pairing.appliance_id
        return pairing

    with (
        patch(
            "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
            return_value=[discovery],
        ),
        patch(
            "custom_components.weber_connect.config_flow.generate_identity",
            return_value=identity,
        ),
        patch(
            "custom_components.weber_connect.config_flow.WeberCloudClient",
            FakeCloudClient,
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_pair",
            side_effect=delayed_pairing,
        ),
    ):
        FakeCloudClient.association_codes.clear()
        FakeCloudClient.associated_appliance_id = None
        FakeCloudClient.events.clear()
        FakeCloudClient.timeouts.clear()
        result = await hass.config_entries.flow.async_init(  # type: ignore[attr-defined]
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(  # type: ignore[attr-defined]
            result["flow_id"],
            {CONF_ADDRESS: ADDRESS},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "confirm"

        result = await hass.config_entries.flow.async_configure(  # type: ignore[attr-defined]
            result["flow_id"],
            {},
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS

        await asyncio.sleep(0.05)
        if not pairing_started.is_set():
            result = await hass.config_entries.flow.async_configure(result["flow_id"])  # type: ignore[attr-defined]
        await asyncio.wait_for(pairing_started.wait(), timeout=1.0)
        allow_pairing.set()
        result = await _finish_progress(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Weber Connect Hub"
    assert result["data"][CONF_COMPANION_ID] == identity.companion_id
    assert result["data"][CONF_APPLIANCE_ID] == pairing.appliance_id
    assert result["data"][CONF_CLOUD_PASSWORD]
    assert FakeCloudClient.association_codes == []
    assert FakeCloudClient.events[:2] == ["cloud_registered", "bluetooth_pairing"]
    assert FakeCloudClient.timeouts == [5.0, 5.0]


@pytest.mark.asyncio
async def test_user_flow_explains_when_no_hub_is_visible(hass: object) -> None:
    with patch(
        "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(  # type: ignore[attr-defined]
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_devices"
    assert result["menu_options"] == ["search_again"]


@pytest.mark.asyncio
async def test_user_flow_waits_for_delayed_cloud_association() -> None:
    """A propagation race must finish automatically without blaming connectivity."""

    identity = CompanionIdentity(
        companion_id="11" * 16,
        public_key="33" * 64,
    )
    pairing = PairingResult(
        message_version=10,
        appliance_id="44" * 16,
    )
    EventuallyAssociatedCloudClient.appliance_id = pairing.appliance_id
    EventuallyAssociatedCloudClient.checks = 0

    class ImmediateHass:
        async def async_add_executor_job(
            self, target: Callable[..., object], *args: object
        ) -> object:
            return target(*args)

    flow = WeberConnectConfigFlow()
    flow.hass = ImmediateHass()  # type: ignore[assignment]
    flow._address = ADDRESS
    flow._identity = identity
    flow._pairing_result = pairing
    flow._cloud_config = CloudConfig.generate(identity.companion_id)

    with (
        patch(
            "custom_components.weber_connect.config_flow.WeberCloudClient",
            EventuallyAssociatedCloudClient,
        ),
        patch(
            "custom_components.weber_connect.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
        patch(
            "custom_components.weber_connect.config_flow._monotonic_time",
            side_effect=[0.0, 0.0, 10.0],
        ),
    ):
        result = await flow._async_cloud_setup()

    assert result[CONF_APPLIANCE_ID] == pairing.appliance_id
    assert EventuallyAssociatedCloudClient.checks == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_pairing_timeout_has_clear_retry_without_new_identity(hass: object) -> None:
    discovery = SimpleNamespace(
        address=ADDRESS,
        name="Weber Connect Hub",
        manufacturer_data={0x0DF2: b"weber"},
    )
    identity = CompanionIdentity(
        companion_id="11" * 16,
        public_key="33" * 64,
    )
    pairing = PairingResult(
        message_version=10,
        appliance_id="44" * 16,
    )
    pairing_started = asyncio.Event()
    finish_first_attempt = asyncio.Event()
    attempt = 0

    async def controlled_pairing(*_args: object, **_kwargs: object) -> PairingResult:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            pairing_started.set()
            await finish_first_attempt.wait()
            raise WeberBluetoothError("The hub returned TIMED_OUT for pairing.")
        FakeCloudClient.associated_appliance_id = pairing.appliance_id
        return pairing

    pair = AsyncMock(side_effect=controlled_pairing)
    FakeCloudClient.associated_appliance_id = None
    with (
        patch(
            "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
            return_value=[discovery],
        ),
        patch(
            "custom_components.weber_connect.config_flow.generate_identity",
            return_value=identity,
        ) as generate,
        patch(
            "custom_components.weber_connect.config_flow.WeberCloudClient",
            FakeCloudClient,
        ),
        patch(
            "custom_components.weber_connect.config_flow.async_pair",
            new=pair,
        ),
    ):
        result = await hass.config_entries.flow.async_init(  # type: ignore[attr-defined]
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        result = await hass.config_entries.flow.async_configure(  # type: ignore[attr-defined]
            result["flow_id"],
            {CONF_ADDRESS: ADDRESS},
        )
        result = await hass.config_entries.flow.async_configure(  # type: ignore[attr-defined]
            result["flow_id"],
            {},
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS

        await asyncio.sleep(0.05)
        if not pairing_started.is_set():
            result = await hass.config_entries.flow.async_configure(result["flow_id"])  # type: ignore[attr-defined]
        await asyncio.wait_for(pairing_started.wait(), timeout=1.0)
        finish_first_attempt.set()
        await hass.async_block_till_done()  # type: ignore[attr-defined]
        result = await hass.config_entries.flow.async_configure(  # type: ignore[attr-defined]
            result["flow_id"],
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "pairing_failed"

        result = await hass.config_entries.flow.async_configure(  # type: ignore[attr-defined]
            result["flow_id"],
            {"next_step_id": "retry_pairing"},
        )
        result = await _finish_progress(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert pair.await_count == 2
    generate.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_saves_and_reloads_through_home_assistant(hass: object) -> None:
    """Exercise the framework contract that previously raised a production HTTP 500."""

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options=WeberOptions().as_dict(),
        unique_id=ADDRESS,
    )
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(
        initial_state=lambda: {"source": "cloud", "connected": False},
        async_set_updated_data=Mock(),
        async_start=lambda: None,
    )
    submitted = {
        CONF_PROBES: {"probe_name_1": "Brisket"},
    }

    with (
        patch(
            "custom_components.weber_connect.WeberCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            hass.config_entries,  # type: ignore[attr-defined]
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
        patch.object(
            hass.config_entries,  # type: ignore[attr-defined]
            "async_reload",
            new=AsyncMock(return_value=True),
        ) as reload_entry,
    ):
        assert await async_setup_entry(hass, entry)  # type: ignore[arg-type]
        result = await hass.config_entries.options.async_init(entry.entry_id)  # type: ignore[attr-defined]
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.options.async_configure(  # type: ignore[attr-defined]
            result["flow_id"], submitted
        )
        await hass.async_block_till_done()  # type: ignore[attr-defined]

    assert result["type"] is FlowResultType.CREATE_ENTRY
    saved = WeberOptions.from_mapping(entry.options)
    assert saved.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    assert saved.probe_name(1) == "Brisket"
    coordinator.async_set_updated_data.assert_called_once()
    reload_entry.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("wrong_hub", [False, True])
async def test_reauth_preserves_entry_and_requires_same_appliance(
    hass: Any, wrong_hub: bool
) -> None:
    """A completed pairing replaces credentials only for the original appliance."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Patio grill",
        unique_id=ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_APPLIANCE_ID: "44" * 16,
            CONF_COMPANION_ID: "old",
            CONF_CLOUD_PASSWORD: "old-password",
        },
        options=WeberOptions(probe_names=("Brisket", "", "", "Spare")).as_dict(),
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, ADDRESS)}, name="Patio grill"
    )
    registered = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{ADDRESS}_probe_1_temperature", config_entry=entry, device_id=device.id
    )
    registered = er.async_get(hass).async_update_entity(registered.entity_id, name="Brisket")
    original_data, original_options = dict(entry.data), dict(entry.options)
    appliance = "55" * 16 if wrong_hub else "44" * 16
    FakeCloudClient.associated_appliance_id = appliance
    with (
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", FakeCloudClient),
        patch(
            "custom_components.weber_connect.config_flow.async_pair",
            new=AsyncMock(return_value=PairingResult(message_version=10, appliance_id=appliance)),
        ),
        patch.object(
            hass.config_entries, "async_reload", new=AsyncMock(return_value=True)
        ) as reload,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=dict(entry.data),
        )
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "confirm"
        assert dict(entry.data) == original_data
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await _finish_progress(hass, result)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == ("wrong_hub" if wrong_hub else "reauth_successful")
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.title == "Patio grill" and entry.unique_id == ADDRESS
    assert dict(entry.options) == original_options
    assert er.async_get(hass).async_get(registered.entity_id) == registered
    assert dr.async_get(hass).async_get(device.id) == device
    if wrong_hub:
        assert dict(entry.data) == original_data
        reload.assert_not_called()
    else:
        assert entry.data[CONF_COMPANION_ID] != "old"
        reload.assert_awaited_once_with(entry.entry_id)


async def test_cancel_reauth_keeps_original_configuration(hass: Any) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_APPLIANCE_ID: "original"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.data[CONF_APPLIANCE_ID] == "original"


async def test_options_use_registered_slots_and_preserve_hidden_names(hass: Any) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={},
        options=WeberOptions(probe_names=("", "", "Hidden", "Spare")).as_dict(),
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{ADDRESS}_probe_4_temperature", config_entry=entry
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    validated = schema({CONF_PROBES: {}})
    assert set(validated[CONF_PROBES]) == {"probe_name_1", "probe_name_2", "probe_name_4"}
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock(return_value=True)):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PROBES: {"probe_name_1": "Brisket"}}
        )
        await hass.async_block_till_done()
    saved = WeberOptions.from_mapping(entry.options)
    assert saved.probe_names == ("Brisket", "", "Hidden", "Spare")


async def test_reauth_retry_stays_locked_to_original_hub(hass: Any) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_APPLIANCE_ID: "original"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    menu = await flow.async_step_pairing_failed()
    assert menu["menu_options"] == ["retry_pairing", "start_over"]
    for retry in (flow.async_step_start_over, flow.async_step_choose_hub):
        result = await retry()
        assert result["step_id"] == "reauth_confirm"
        assert flow._address == ADDRESS
        assert flow._identity is None
    hass.config_entries.flow.async_abort(flow.flow_id)

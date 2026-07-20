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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect import async_setup_entry
from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.config_flow import WeberConnectConfigFlow
from custom_components.weber_connect.const import (
    CONF_ADVANCED,
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_LOCAL_FALLBACK,
    CONF_POLL_SECONDS,
    CONF_PROBES,
    DOMAIN,
)
from custom_components.weber_connect.models import CompanionIdentity, PairingResult
from custom_components.weber_connect.options import ConnectionMode, WeberOptions

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

    def __init__(self, config: object) -> None:
        self.config = config
        self.authenticated = False

    def authenticate(self) -> str:
        self.authenticated = True
        return "token"

    def close(self) -> None:
        return None

    def associated_appliances(self) -> list[dict[str, object]]:
        return []

    def associate(self, verification_code: str) -> dict[str, object]:
        self.association_codes.append(verification_code)
        return {"associated": True}


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
        private_key="22" * 64,
        public_key="33" * 64,
    )
    pairing = PairingResult(
        message_version=10,
        appliance_id="44" * 16,
        appliance_public_key="55" * 64,
        verification_code=123456,
    )
    pairing_started = asyncio.Event()
    allow_pairing = asyncio.Event()

    async def delayed_pairing(*_args: object, **_kwargs: object) -> PairingResult:
        pairing_started.set()
        await allow_pairing.wait()
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

        await pairing_started.wait()
        allow_pairing.set()
        result = await _finish_progress(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Weber Connect Hub"
    assert result["data"][CONF_COMPANION_ID] == identity.companion_id
    assert result["data"][CONF_APPLIANCE_ID] == pairing.appliance_id
    assert result["data"][CONF_CLOUD_PASSWORD]
    assert FakeCloudClient.association_codes == ["123456"]


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
        private_key="22" * 64,
        public_key="33" * 64,
    )
    pairing = PairingResult(
        message_version=10,
        appliance_id="44" * 16,
        appliance_public_key="55" * 64,
        verification_code=None,
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

    with (
        patch(
            "custom_components.weber_connect.config_flow.WeberCloudClient",
            EventuallyAssociatedCloudClient,
        ),
        patch(
            "custom_components.weber_connect.config_flow.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
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
        private_key="22" * 64,
        public_key="33" * 64,
    )
    pairing = PairingResult(
        message_version=10,
        appliance_id="44" * 16,
        appliance_public_key="55" * 64,
        verification_code=123456,
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
        return pairing

    pair = AsyncMock(side_effect=controlled_pairing)
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

        await pairing_started.wait()
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
        cloud_enabled=False,
        async_set_updated_data=Mock(),
        async_start=lambda: None,
    )
    submitted = {
        CONF_CONNECTION: {
            CONF_CONNECTION_MODE: ConnectionMode.HOME_ASSISTANT_ONLY.value,
        },
        CONF_PROBES: {},
        CONF_ADVANCED: {
            CONF_POLL_SECONDS: "10",
            CONF_LOCAL_FALLBACK: False,
        },
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
    assert WeberOptions.from_mapping(entry.options) == WeberOptions(
        connection_mode=ConnectionMode.HOME_ASSISTANT_ONLY
    )
    coordinator.async_set_updated_data.assert_called_once()
    reload_entry.assert_awaited_once_with(entry.entry_id)

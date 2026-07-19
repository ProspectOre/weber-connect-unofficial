"""Config-flow recovery, discovery, and options edge contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.weber_connect.bluetooth import WeberBluetoothError
from custom_components.weber_connect.config_flow import (
    OptionsFlow,
    WeberConnectConfigFlow,
    _is_weber,
)
from custom_components.weber_connect.const import (
    CONF_ADVANCED,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
    CONF_LOCAL_FALLBACK,
    CONF_POLL_SECONDS,
    CONF_PROBES,
    CONF_REMOTE_CONTROLS,
)
from custom_components.weber_connect.models import CompanionIdentity, PairingResult
from custom_components.weber_connect.options import ConnectionMode, WeberOptions
from custom_components.weber_connect.weber_cloud import WeberCloudError

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ADDRESS = "AA:BB:CC:DD:EE:FF"
IDENTITY = CompanionIdentity("11" * 16, "22" * 64, "33" * 64)
PAIRING = PairingResult(10, "44" * 16, "55" * 64, None)


def flow(hass: object) -> WeberConnectConfigFlow:
    instance = WeberConnectConfigFlow()
    instance.hass = hass  # type: ignore[assignment]
    instance.context = {}
    return instance


def test_weber_detection_and_discovery_labels_cover_adapter_and_proxy_paths(hass: object) -> None:
    instance = flow(hass)
    assert _is_weber(SimpleNamespace(manufacturer_data={0x0DF2: b"x"}, name="Unknown"))
    assert _is_weber(SimpleNamespace(manufacturer_data={}, name="June Oven"))
    assert not _is_weber(SimpleNamespace(manufacturer_data={}, name="Other"))

    direct = SimpleNamespace(address=ADDRESS, name=None, source="")
    assert instance._discovery_path(direct) == "Home Assistant Bluetooth"
    assert instance._discovery_label(direct) == f"Weber hub {ADDRESS}"

    proxy = SimpleNamespace(address=ADDRESS, name="Weber Hub", source="proxy-source")
    scanner = SimpleNamespace(name="Patio Proxy")
    with patch(
        "custom_components.weber_connect.config_flow.bluetooth.async_scanner_by_source",
        return_value=scanner,
    ):
        assert instance._discovery_path(proxy) == "Patio Proxy"
        assert instance._discovery_label(proxy) == "Weber Hub · via Patio Proxy"

    proxy.name = "Weber Hub via Patio Proxy"
    with patch(
        "custom_components.weber_connect.config_flow.bluetooth.async_scanner_by_source",
        return_value=scanner,
    ):
        assert instance._discovery_label(proxy) == proxy.name

    with patch(
        "custom_components.weber_connect.config_flow.bluetooth.async_scanner_by_source",
        return_value=SimpleNamespace(name=""),
    ):
        assert instance._discovery_path(proxy) == "Home Assistant Bluetooth"


@pytest.mark.asyncio
async def test_bluetooth_discovery_and_search_again_paths(hass: object) -> None:
    instance = flow(hass)
    discovery = SimpleNamespace(
        address=ADDRESS,
        name="Weber Hub",
        source="proxy",
        manufacturer_data={0x0DF2: b"x"},
    )
    instance.async_set_unique_id = AsyncMock()
    instance._abort_if_unique_id_configured = MagicMock()
    with patch(
        "custom_components.weber_connect.config_flow.bluetooth.async_scanner_by_source",
        return_value=SimpleNamespace(name="Patio Proxy"),
    ):
        result = await instance.async_step_bluetooth(discovery)  # type: ignore[arg-type]
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["path"] == "Patio Proxy"

    with patch.object(
        instance, "async_step_user", AsyncMock(return_value={"type": "done"})
    ) as user:
        assert await instance.async_step_search_again() == {"type": "done"}
        user.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_and_pairing_recovery_impossible_states_are_safe(hass: object) -> None:
    instance = flow(hass)
    result = await instance.async_step_confirm()
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_devices"

    with pytest.raises(WeberBluetoothError, match="no longer visible"):
        instance._start_pairing()

    instance._address = ADDRESS
    sentinel = MagicMock()
    instance._pairing_task = sentinel
    instance._start_pairing()
    assert instance._pairing_task is sentinel

    instance._pairing_task = None
    with patch.object(instance, "_start_pairing", side_effect=WeberBluetoothError("gone")):
        result = await instance.async_step_pairing()
    assert result["step_id"] == "pairing_failed"

    with patch.object(instance, "_start_pairing", return_value=None):
        result = await instance.async_step_pairing()
    assert result["step_id"] == "pairing_failed"


@pytest.mark.asyncio
async def test_pairing_and_cloud_unexpected_failures_choose_recoverable_steps(hass: object) -> None:
    instance = flow(hass)
    instance._address = ADDRESS
    failed_pairing = hass.async_create_task(asyncio.sleep(0, result=None))  # type: ignore[attr-defined]
    await failed_pairing
    failed_pairing = MagicMock()
    failed_pairing.done.return_value = True
    failed_pairing.__await__ = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    instance._pairing_task = failed_pairing
    with patch.object(instance, "_start_pairing"):
        result = await instance.async_step_pairing()
    assert result["step_id"] == "setup_failed"

    instance = flow(hass)
    failed_cloud = hass.async_create_task(asyncio.sleep(0, result=None))  # type: ignore[attr-defined]
    await failed_cloud
    failed_cloud = MagicMock()
    failed_cloud.done.return_value = True
    failed_cloud.__await__ = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    instance._cloud_task = failed_cloud
    with patch.object(instance, "_start_cloud_setup"):
        result = await instance.async_step_cloud()
    assert result["step_id"] == "setup_failed"


@pytest.mark.asyncio
async def test_cloud_setup_missing_state_eventual_timeout_and_close(hass: object) -> None:
    instance = flow(hass)
    with pytest.raises(WeberCloudError, match="Physical pairing"):
        await instance._async_cloud_setup()

    instance._address = ADDRESS
    instance._identity = IDENTITY
    instance._pairing_result = PAIRING
    client = MagicMock()
    client.authenticate.return_value = "token"
    client.associated_appliances.return_value = []
    with (
        patch("custom_components.weber_connect.config_flow.WeberCloudClient", return_value=client),
        patch.object(instance, "_async_wait_for_cloud_association", AsyncMock(return_value=None)),
    ):
        with pytest.raises(WeberCloudError, match="has not finished"):
            await instance._async_cloud_setup()
    client.close.assert_called_once()

    immediate_hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda target, *args: target(*args))
    )
    instance.hass = immediate_hass  # type: ignore[assignment]
    with patch("custom_components.weber_connect.config_flow.asyncio.sleep", AsyncMock()) as sleep:
        assert (
            await instance._async_wait_for_cloud_association(client, PAIRING.appliance_id) is None
        )
    assert sleep.await_count == 4


@pytest.mark.asyncio
async def test_cloud_progress_missing_task_known_error_and_idempotent_start(hass: object) -> None:
    instance = flow(hass)
    with patch.object(instance, "_start_cloud_setup", return_value=None):
        result = await instance.async_step_cloud()
    assert result["step_id"] == "cloud_failed"

    async def fail() -> dict[str, object]:
        raise WeberCloudError("offline")

    task = hass.async_create_task(fail())  # type: ignore[attr-defined]
    await asyncio.sleep(0)
    instance._cloud_task = task
    with patch.object(instance, "_start_cloud_setup"):
        result = await instance.async_step_cloud()
    assert result["step_id"] == "cloud_failed"

    sentinel = MagicMock()
    instance._cloud_task = sentinel
    instance._start_cloud_setup()
    assert instance._cloud_task is sentinel


@pytest.mark.asyncio
async def test_recovery_menus_reset_complete_and_options(hass: object) -> None:
    instance = flow(hass)
    assert (await instance.async_step_pairing_failed())["menu_options"] == [
        "retry_pairing",
        "choose_hub",
    ]
    assert (await instance.async_step_cloud_failed())["menu_options"] == [
        "retry_cloud",
        "start_over",
    ]
    assert (await instance.async_step_setup_failed())["menu_options"] == ["start_over"]

    instance._address = ADDRESS
    instance._identity = IDENTITY
    instance._pairing_result = PAIRING
    instance._entry_data = {"ready": True}
    with patch.object(instance, "async_step_user", AsyncMock(return_value={"type": "user"})):
        assert await instance.async_step_choose_hub() == {"type": "user"}
    assert instance._address is None
    assert instance._identity is None
    assert instance._entry_data is None

    instance._address = ADDRESS
    with patch.object(instance, "async_step_user", AsyncMock(return_value={"type": "user"})):
        assert await instance.async_step_start_over() == {"type": "user"}

    with patch.object(
        instance, "async_step_setup_failed", AsyncMock(return_value={"type": "failed"})
    ):
        assert await instance.async_step_complete() == {"type": "failed"}

    instance._entry_data = {"ready": True}
    instance._name = "Hub"
    with patch.object(instance, "async_create_entry", return_value={"type": "created"}) as create:
        assert await instance.async_step_complete() == {"type": "created"}
        create.assert_called_once_with(title="Hub", data={"ready": True})

    options = OptionsFlow()
    options.hass = hass  # type: ignore[assignment]
    with patch.object(
        OptionsFlow,
        "config_entry",
        new_callable=PropertyMock,
        return_value=SimpleNamespace(options=WeberOptions().as_dict()),
    ):
        form = await options.async_step_init()
    assert form["type"] is FlowResultType.FORM
    submitted = {
        CONF_CONNECTION: {
            CONF_CONNECTION_MODE: ConnectionMode.PHONE_AND_HOME_ASSISTANT,
            CONF_REMOTE_CONTROLS: False,
        },
        CONF_PROBES: {},
        CONF_ADVANCED: {CONF_POLL_SECONDS: "10", CONF_LOCAL_FALLBACK: False},
    }
    with patch.object(options, "async_create_entry", return_value={"type": "created"}) as create:
        assert await options.async_step_init(submitted) == {"type": "created"}
        create.assert_called_once_with(title="", data=submitted)

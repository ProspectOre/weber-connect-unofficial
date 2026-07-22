"""Single-transport coordinator, recovery, and diagnostics contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.weber_connect import coordinator as coordinator_module
from custom_components.weber_connect.const import (
    CONF_APPLIANCE_ID,
    CONF_CLOUD_PASSWORD,
    CONF_COMPANION_ID,
    CONF_CONNECTION,
    CONF_CONNECTION_MODE,
)
from custom_components.weber_connect.coordinator import WeberCoordinator
from custom_components.weber_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.weber_connect.models import WeberRuntimeData
from custom_components.weber_connect.options import ConnectionMode

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeTransport:
    """Entry-owned session double with controllable callbacks."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.status_callback: object = None
        self.error_callback: object = None
        self.closed = False
        self.wake_count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.received_types = [0x80]
        self.error_kind = "connection"

    async def async_run(self, status_callback: object, error_callback: object) -> None:
        self.status_callback = status_callback
        self.error_callback = error_callback
        self.started.set()
        await self.release.wait()

    def async_wake(self) -> None:
        self.wake_count += 1

    async def async_close(self) -> None:
        self.closed = True
        self.release.set()


class FakeCloudClient:
    def __init__(self, config: object) -> None:
        self.config = config
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _entry(hass: object, *, cloud: bool = True) -> SimpleNamespace:
    mode = ConnectionMode.PHONE_AND_HOME_ASSISTANT if cloud else ConnectionMode.HOME_ASSISTANT_ONLY
    return SimpleNamespace(
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_COMPANION_ID: "11" * 16,
            CONF_CLOUD_PASSWORD: "cloud-password",
            CONF_APPLIANCE_ID: "22" * 16,
        },
        options={CONF_CONNECTION: {CONF_CONNECTION_MODE: mode.value}},
        entry_id="test-entry",
        unique_id="AA:BB:CC:DD:EE:FF",
        title="Test Weber Hub",
        pref_disable_polling=False,
        async_create_background_task=lambda _hass, coro, name: hass.async_create_task(  # type: ignore[attr-defined]
            coro, name=name
        ),
        async_on_unload=MagicMock(),
    )


def _coordinator(hass: object, *, cloud: bool) -> tuple[WeberCoordinator, FakeTransport]:
    transport = FakeTransport()
    with (
        patch.object(coordinator_module, "WeberCloudClient", FakeCloudClient),
        patch.object(
            coordinator_module,
            "WeberCloudSession" if cloud else "WeberBluetoothSession",
            return_value=transport,
        ),
    ):
        coordinator = WeberCoordinator(hass, _entry(hass, cloud=cloud))  # type: ignore[arg-type]
    return coordinator, transport


def test_cloud_mode_constructs_only_cloud_transport(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=True)
    assert coordinator.source == "cloud"
    assert coordinator.cloud_session is transport
    assert coordinator.bluetooth_session is None
    assert coordinator.cloud_client is not None


def test_local_mode_constructs_only_bluetooth_transport(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=False)
    assert coordinator.source == "bluetooth"
    assert coordinator.bluetooth_session is transport
    assert coordinator.cloud_session is None
    assert coordinator.cloud_client is None


@pytest.mark.asyncio
async def test_status_publishes_four_slot_state_and_clears_failure(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator._async_error("temporary")
    coordinator._async_status(
        {
            "probes": [
                {
                    "probe_number": 2,
                    "probe_temp_c": 25.0,
                    "state": "PROBED",
                }
            ]
        }
    )
    assert coordinator.data["probe_2_temperature"] == 25.0
    assert coordinator.data["probe_1_temperature"] is None
    assert coordinator.data["source"] == "cloud"
    assert coordinator.last_error is None
    assert coordinator.consecutive_failures == 0
    assert coordinator.successful_updates == 1


def test_three_failures_clear_stale_temperature_to_honest_unknown(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=False)
    coordinator._async_status({"probes": [{"probe_number": 4, "probe_temp_c": 30.0}]})
    for _ in range(coordinator_module.OFFLINE_FAILURE_THRESHOLD - 1):
        coordinator._async_error("hub sleeping")
    assert coordinator.data["probe_4_temperature"] == 30.0
    coordinator._async_error("hub sleeping")
    assert coordinator.data["probe_4_temperature"] is None
    assert coordinator.data["connected"] is False
    assert coordinator.failed_updates == coordinator_module.OFFLINE_FAILURE_THRESHOLD


def test_local_idle_never_creates_a_repair(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=False)
    for _ in range(20):
        coordinator._async_error("hub is asleep")
    issue_id = f"connection_lost_{coordinator.entry.entry_id}"
    assert ir.async_get(hass).async_get_issue("weber_connect", issue_id) is None


def test_cloud_outage_never_creates_a_repair(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    for _ in range(20):
        coordinator._async_error("hub powered off")
    issue_id = f"connection_lost_{coordinator.entry.entry_id}"
    assert ir.async_get(hass).async_get_issue("weber_connect", issue_id) is None
    assert coordinator.data["connected"] is False


def test_rejected_cloud_credential_creates_distinct_immediate_repair(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=True)
    transport.error_kind = "credentials"

    coordinator._async_error("credential rejected")

    registry = ir.async_get(hass)
    credential_issue = f"credentials_rejected_{coordinator.entry.entry_id}"
    connection_issue = f"connection_lost_{coordinator.entry.entry_id}"
    assert registry.async_get_issue("weber_connect", credential_issue) is not None
    assert registry.async_get_issue("weber_connect", connection_issue) is None

    transport.error_kind = "connection"
    for _ in range(20):
        coordinator._async_error("network unavailable")
    assert registry.async_get_issue("weber_connect", credential_issue) is not None
    assert registry.async_get_issue("weber_connect", connection_issue) is None

    transport.error_kind = "connection"
    coordinator._async_status({"probes": []})
    assert registry.async_get_issue("weber_connect", credential_issue) is None


def test_bluetooth_advertisement_wakes_existing_session_only(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=False)
    coordinator._async_bluetooth_advertisement(MagicMock(), MagicMock())
    coordinator._async_bluetooth_advertisement(MagicMock(), MagicMock())
    assert transport.wake_count == 2


@pytest.mark.asyncio
async def test_start_and_close_own_exactly_one_transport_task(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=False)
    legacy_issue = f"connection_lost_{coordinator.entry.entry_id}"
    ir.async_create_issue(
        hass,
        "weber_connect",
        legacy_issue,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="connection_lost",
    )
    assert ir.async_get(hass).async_get_issue("weber_connect", legacy_issue) is not None
    cancel_callback = MagicMock()
    with patch.object(
        coordinator_module.bluetooth,
        "async_register_callback",
        return_value=cancel_callback,
    ) as register:
        coordinator.async_start()
        coordinator.async_start()
    await transport.started.wait()
    assert coordinator._transport_task is not None
    assert ir.async_get(hass).async_get_issue("weber_connect", legacy_issue) is None
    register.assert_called_once()

    await coordinator.async_close()
    cancel_callback.assert_called_once_with()
    assert transport.closed is True
    assert coordinator._transport_task is None


@pytest.mark.asyncio
async def test_cloud_close_releases_socket_then_discards_token(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=True)
    client = coordinator.cloud_client
    assert isinstance(client, FakeCloudClient)
    coordinator.async_start()
    await transport.started.wait()
    await coordinator.async_close()
    assert transport.closed is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_diagnostics_are_minimal_and_redact_legacy_and_current_secrets(
    hass: object,
) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator._async_status({"probes": [{"probe_number": 1, "probe_temp_c": 63.5}]})
    entry = _entry(hass, cloud=True)
    entry.data["companion_private_key"] = "private-key"
    entry.data["companion_public_key"] = "public-key"
    entry.runtime_data = WeberRuntimeData(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)  # type: ignore[arg-type]
    serialized = str(diagnostics)
    assert "cloud-password" not in serialized
    assert "private-key" not in serialized
    assert "public-key" not in serialized
    assert diagnostics["transport"] == "cloud"
    assert diagnostics["probe_slots"][0]["temperature_c"] == 63.5
    assert diagnostics["cloud_socket_received_types"] == [0x80]
    assert "state" not in diagnostics
    assert "cloud_history_schema" not in diagnostics

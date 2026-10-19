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
        self.socket_connections = 2
        self.fast_recoveries = 1
        self.last_error_type = "TimeoutError"
        self.fuel_level_reports = 12
        self.fuel_level_unknown_reports = 3
        self.fuel_level_changes = 5
        self.last_recovery_seconds = 12.5
        self.max_recovery_seconds = 25.0

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
    mode = ConnectionMode.PHONE_AND_HOME_ASSISTANT.value if cloud else "home_assistant_only"
    return SimpleNamespace(
        data={
            "address": "AA:BB:CC:DD:EE:FF",
            CONF_COMPANION_ID: "11" * 16,
            CONF_CLOUD_PASSWORD: "cloud-password",
            CONF_APPLIANCE_ID: "22" * 16,
        },
        options={CONF_CONNECTION: {CONF_CONNECTION_MODE: mode}},
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
            "WeberCloudSession",
            return_value=transport,
        ),
    ):
        coordinator = WeberCoordinator(hass, _entry(hass, cloud=cloud))  # type: ignore[arg-type]
    return coordinator, transport


def test_cloud_mode_constructs_only_cloud_transport(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=True)
    assert coordinator.source == "cloud"
    assert coordinator.cloud_session is transport
    assert coordinator.cloud_client is not None


def test_retired_local_mode_migrates_fail_closed_to_cloud_transport(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=False)
    assert coordinator.options.connection_mode is ConnectionMode.PHONE_AND_HOME_ASSISTANT
    assert coordinator.source == "cloud"
    assert coordinator.cloud_session is transport
    assert coordinator.cloud_client is not None


@pytest.mark.asyncio
async def test_status_publishes_grill_and_reported_probe_state_and_clears_failure(
    hass: object,
) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator._async_error("temporary")
    coordinator._async_status(
        {
            "actual_cavity_temp_c": 121.5,
            "probes": [
                {
                    "probe_number": 2,
                    "probe_temp_c": 25.0,
                    "state": "PROBED",
                }
            ],
        }
    )
    assert coordinator.data["probe_2_temperature"] == 25.0
    assert coordinator.data["probe_1_temperature"] is None
    assert coordinator.data["reported_probe_numbers"] == (2,)
    assert coordinator.data["grill_temperature"] == 121.5
    assert coordinator.data["source"] == "cloud"
    assert coordinator.last_error is None
    assert coordinator.consecutive_failures == 0
    assert coordinator.successful_updates == 1


def test_three_cloud_failures_retain_only_slow_changing_hub_state(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator._async_status(
        {
            "actual_cavity_temp_c": 110.0,
            "battery_level": 64,
            "is_charging": True,
            "wifi_signal_strength": -61,
            "wifi_connection_status": "connected",
            "cloud_connection_status": "connected",
            "device_state": "idle",
            "probes": [{"probe_number": 4, "probe_temp_c": 30.0}],
        }
    )
    last_successful_update = coordinator.data["last_successful_update"]

    for _ in range(coordinator_module.OFFLINE_FAILURE_THRESHOLD + 15):
        coordinator._async_error("cloud relay unavailable")

    assert coordinator.data["connected"] is False
    assert coordinator.data["last_successful_update"] == last_successful_update
    assert coordinator.data["battery_level"] == 64
    assert coordinator.data["is_charging"] is True
    assert coordinator.data["wifi_signal_strength"] == -61
    assert coordinator.data["wifi_connection_status"] == "connected"
    assert coordinator.data["cloud_connection_status"] is None
    assert coordinator.data["last_reported_cloud_connection_status"] == "connected"
    assert coordinator.data["device_state"] == "idle"
    assert coordinator.data["grill_temperature"] is None
    assert coordinator.data["probe_4_temperature"] is None
    assert coordinator.failed_updates == coordinator_module.OFFLINE_FAILURE_THRESHOLD + 15

    coordinator._async_status(
        {
            "battery_level": 63,
            "is_charging": False,
            "wifi_signal_strength": -55,
            "wifi_connection_status": "connected",
            "cloud_connection_status": "connected",
            "device_state": "active",
            "probes": [],
        }
    )
    assert coordinator.data["connected"] is True
    assert coordinator.data["battery_level"] == 63
    assert coordinator.data["is_charging"] is False
    assert coordinator.data["device_state"] == "active"
    assert coordinator.consecutive_failures == 0
    assert coordinator.last_error is None


def test_cloud_outage_never_creates_a_repair(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    for _ in range(20):
        coordinator._async_error("hub powered off")
    issue_id = f"connection_lost_{coordinator.entry.entry_id}"
    assert ir.async_get(hass).async_get_issue("weber_connect", issue_id) is None
    assert coordinator.data["connected"] is False


def test_status_updates_device_firmware_metadata(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    registry = MagicMock()
    devices = [
        SimpleNamespace(
            id="device-id", identifiers={("weber_connect", coordinator.entry.unique_id)}
        )
    ]

    with (
        patch.object(coordinator_module.dr, "async_get", return_value=registry),
        patch.object(
            coordinator_module.dr, "async_entries_for_config_entry", return_value=devices
        ) as entries,
    ):
        coordinator._async_status(
            {
                "software_version": "2.0.3_7398",
                "hardware_version": "rev-b",
                "probes": [],
            }
        )

    entries.assert_called_once_with(registry, coordinator.entry.entry_id)
    registry.async_get_device.assert_not_called()
    registry.async_update_device.assert_called_once_with(
        "device-id",
        sw_version="2.0.3_7398",
        hw_version="rev-b",
    )


@pytest.mark.parametrize(
    ("status", "expected_update"),
    [
        ({"software_version": "2.0.3_7398"}, {"sw_version": "2.0.3_7398"}),
        ({"hardware_version": "rev-b"}, {"hw_version": "rev-b"}),
    ],
)
def test_status_updates_only_reported_device_version(
    hass: object,
    status: dict[str, str],
    expected_update: dict[str, str],
) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    registry = MagicMock()
    devices = [
        SimpleNamespace(
            id="device-id", identifiers={("weber_connect", coordinator.entry.unique_id)}
        )
    ]

    with (
        patch.object(coordinator_module.dr, "async_get", return_value=registry),
        patch.object(
            coordinator_module.dr, "async_entries_for_config_entry", return_value=devices
        ) as entries,
    ):
        coordinator._async_status({**status, "probes": []})

    entries.assert_called_once_with(registry, coordinator.entry.entry_id)
    registry.async_update_device.assert_called_once_with("device-id", **expected_update)


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


@pytest.mark.asyncio
async def test_start_and_close_own_exactly_one_transport_task(hass: object) -> None:
    coordinator, transport = _coordinator(hass, cloud=True)
    legacy_issue = f"connection_lost_{coordinator.entry.entry_id}"
    orphaned_legacy_issue = "connection_lost_removed-entry"
    credential_issue = f"credentials_rejected_{coordinator.entry.entry_id}"
    ir.async_create_issue(
        hass,
        "weber_connect",
        legacy_issue,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="connection_lost",
    )
    ir.async_create_issue(
        hass,
        "weber_connect",
        orphaned_legacy_issue,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="connection_lost",
    )
    ir.async_create_issue(
        hass,
        "weber_connect",
        credential_issue,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="credentials_rejected",
    )
    assert ir.async_get(hass).async_get_issue("weber_connect", legacy_issue) is not None
    coordinator.async_start()
    coordinator.async_start()
    await transport.started.wait()
    assert coordinator._transport_task is not None
    assert ir.async_get(hass).async_get_issue("weber_connect", legacy_issue) is None
    assert ir.async_get(hass).async_get_issue("weber_connect", orphaned_legacy_issue) is None
    assert ir.async_get(hass).async_get_issue("weber_connect", credential_issue) is not None

    await coordinator.async_close()
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
    coordinator._async_status(
        {
            "actual_cavity_temp_c": 121.5,
            "battery_level": 64,
            "is_charging": True,
            "target_cavity_temp_c": 135.0,
            "cook_mode": "smoke_boost",
            "simple_intensity": 7,
            "wifi_signal_strength": -61,
            "wifi_connection_status": "connected",
            "cloud_connection_status": "connected",
            "device_state": "active",
            "fuel_percent": 18,
            "fuel_level": "low",
            "software_version": "2.0.3_7398",
            "hardware_version": "rev-b",
            "probes": [{"probe_number": 1, "probe_temp_c": 63.5}],
        }
    )
    entry = _entry(hass, cloud=True)
    entry.options["unknown_sensitive_option"] = "private-option"
    entry.options["probes"] = {"probe_name_1": "private-nickname"}
    coordinator.last_error = "Request failed with private-error-secret"
    entry.data["unrecognized_secret"] = "private-extra-secret"
    entry.data["companion_private_key"] = "private-key"
    entry.data["companion_public_key"] = "public-key"
    entry.runtime_data = WeberRuntimeData(coordinator=coordinator)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)  # type: ignore[arg-type]
    serialized = str(diagnostics)
    assert "cloud-password" not in serialized
    assert "private-key" not in serialized
    assert "public-key" not in serialized
    for private in (
        "private-option",
        "private-nickname",
        "private-error-secret",
        "private-extra-secret",
    ):
        assert private not in serialized
    assert diagnostics["transport"] == "cloud"
    assert diagnostics["grill_temperature_c"] == 121.5
    assert diagnostics["hub_battery_level"] == 64
    assert diagnostics["hub_is_charging"] is True
    assert diagnostics["target_grill_temperature_c"] == 135.0
    assert diagnostics["cook_mode"] == "smoke_boost"
    assert diagnostics["wifi_signal_strength"] == -61
    assert diagnostics["fuel_percent"] == 18
    assert diagnostics["software_version"] == "2.0.3_7398"
    assert diagnostics["probe_slots"][0]["temperature_c"] == 63.5
    assert diagnostics["cloud_socket_received_types"] == [0x80]
    assert diagnostics["cloud_socket_connections"] == 2
    assert diagnostics["cloud_socket_fast_recoveries"] == 1
    assert diagnostics["cloud_socket_last_error_type"] == "TimeoutError"
    assert diagnostics["cloud_telemetry"] == {
        "fuel_level_reports": 12,
        "fuel_level_unknown_reports": 3,
        "fuel_level_changes": 5,
        "fuel_level_code_counts": dict.fromkeys(("0", "1", "2", "3", "4", "5", "reserved"), 0),
        "fuel_level_invalid_field_count": 0,
        "last_recovery_seconds": 12.5,
        "max_recovery_seconds": 25.0,
    }
    assert diagnostics["support_report"]["cloud_telemetry"] == diagnostics["cloud_telemetry"]
    assert "state" not in diagnostics
    assert "cloud_history_schema" not in diagnostics


def test_reconnect_marks_retained_readings_then_recovers(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator._async_status({"probes": [{"probe_number": 1, "probe_temp_c": 30.0}]})
    timestamp = coordinator.data["last_successful_update"]
    coordinator._async_error("temporary interruption")
    assert coordinator.data["reading_status"] == "reconnecting"
    assert coordinator.data["probe_1_reading_status"] == "reconnecting"
    assert coordinator.data["probe_1_temperature"] == 30.0
    assert coordinator.data["last_successful_update"] == timestamp
    coordinator._async_status({"probes": [{"probe_number": 1, "probe_temp_c": 31.0}]})
    assert coordinator.data["reading_status"] == "receiving"
    assert coordinator.data["probe_1_reading_status"] == "reading"


def test_startup_outage_keeps_waiting_until_first_reading(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    for _ in range(coordinator_module.OFFLINE_FAILURE_THRESHOLD + 4):
        coordinator._async_error("startup request failed")
        assert coordinator.data["reading_status"] == "waiting"
        assert coordinator.data["last_successful_update"] is None
        assert all(
            coordinator.data[f"probe_{number}_reading_status"] == "waiting"
            for number in range(1, 5)
        )
    coordinator._async_status({"probes": [{"probe_number": 1, "probe_temp_c": 30.0}]})
    coordinator._async_error("later interruption")
    assert coordinator.data["reading_status"] == "reconnecting"


@pytest.mark.asyncio
async def test_close_before_start_releases_constructed_clients(hass: object) -> None:
    """Setup cleanup must work before a background session was started."""
    coordinator, transport = _coordinator(hass, cloud=True)
    assert coordinator._transport_task is None
    await coordinator.async_close()
    assert transport.closed
    assert coordinator.cloud_client.closed


def test_error_defenses_do_not_retain_cloud_metadata_for_unknown_source(hass: object) -> None:
    """Synthetic unsupported source exercises the retained defensive guard."""
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator.source = "unsupported"
    coordinator._async_error("unavailable")
    coordinator._async_error("unavailable")
    with patch.object(coordinator_module.ir, "async_create_issue") as issue:
        coordinator._async_error("unavailable")
    assert coordinator.consecutive_failures == 3
    assert coordinator.data["software_version"] is None
    issue.assert_not_called()


def test_error_defense_tolerates_absent_cloud_session(hass: object) -> None:
    """Synthetic missing session must not turn error reporting into an exception."""
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator.cloud_session = None
    with patch.object(coordinator_module.ir, "async_create_issue") as issue:
        coordinator._async_error("session unavailable")
    assert coordinator.last_error == "session unavailable"
    assert coordinator.data["reading_status"] == "waiting"
    issue.assert_not_called()


def test_support_history_records_failures_and_recovery_without_flooding(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator._async_status({"actual_cavity_temp_c": 100.0})
    coordinator._async_status({"actual_cavity_temp_c": 101.0})
    coordinator._async_error("TimeoutError: private-token@example.com")
    coordinator._async_status({"actual_cavity_temp_c": 102.0})
    history = coordinator.support_journal.snapshot()
    assert [event["event"] for event in history] == [
        "receiving_updates",
        "update_failed",
        "receiving_updates",
    ]
    assert history[1]["error"] == "timeout"
    assert "private-token" not in str(history)


@pytest.mark.asyncio
async def test_diagnostics_before_cloud_session_exists_are_safe(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator.async_set_updated_data(coordinator.initial_state())
    coordinator.cloud_session = None
    coordinator.entry.runtime_data = WeberRuntimeData(coordinator=coordinator)
    report = await async_get_config_entry_diagnostics(hass, coordinator.entry)  # type: ignore[arg-type]
    assert report["cloud_socket_connections"] == 0
    assert report["cloud_socket_fast_recoveries"] == 0
    assert report["cloud_socket_last_error_type"] is None
    assert report["cloud_socket_received_types"] == []
    assert report["cloud_telemetry"]["fuel_level_reports"] == 0
    assert report["cloud_telemetry"]["last_recovery_seconds"] is None


def test_metadata_lookup_is_scoped_and_skips_unrelated_identity(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    registry = MagicMock()
    devices = [
        SimpleNamespace(id="other", identifiers={("weber_connect", "other-identity")}),
        SimpleNamespace(id="owned", identifiers={("weber_connect", coordinator.entry.unique_id)}),
    ]
    with (
        patch.object(coordinator_module.dr, "async_get", return_value=registry),
        patch.object(
            coordinator_module.dr, "async_entries_for_config_entry", return_value=devices
        ) as entries,
    ):
        coordinator._async_status({"software_version": "2.0.3_7398"})
    entries.assert_called_once_with(registry, coordinator.entry.entry_id)
    registry.async_get_device.assert_not_called()
    registry.async_update_device.assert_called_once_with("owned", sw_version="2.0.3_7398")


@pytest.mark.asyncio
async def test_diagnostics_list_only_capability_known_or_reported_probe_slots(hass: object) -> None:
    coordinator, _transport = _coordinator(hass, cloud=True)
    coordinator.entry.data["device_capabilities"] = {"probe_count": 0}
    coordinator.entry.runtime_data = WeberRuntimeData(coordinator=coordinator)
    coordinator.async_set_updated_data(coordinator.initial_state())
    empty = await async_get_config_entry_diagnostics(hass, coordinator.entry)  # type: ignore[arg-type]
    assert empty["probe_slots"] == []

    coordinator._async_status({"probes": [{"probe_number": 6, "probe_temp_c": 40.0}]})
    reported = await async_get_config_entry_diagnostics(hass, coordinator.entry)  # type: ignore[arg-type]
    assert [row["number"] for row in reported["probe_slots"]] == [6]
    assert reported["probe_slots"][0]["temperature_c"] == 40.0

    coordinator.entry.data["device_capabilities"] = {"probe_count": 2}
    known = await async_get_config_entry_diagnostics(hass, coordinator.entry)  # type: ignore[arg-type]
    assert [row["number"] for row in known["probe_slots"]] == [1, 2, 6]

    coordinator.data["reported_probe_numbers"] = (6, True, 0, 17, "private-device-data")
    bounded = await async_get_config_entry_diagnostics(hass, coordinator.entry)  # type: ignore[arg-type]
    assert [row["number"] for row in bounded["probe_slots"]] == [1, 2, 6]
    assert "private-device-data" not in str(bounded)

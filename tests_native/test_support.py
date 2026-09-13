"""Support reports must work before setup and must never export raw secrets."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.weber_connect.config_flow import WeberConnectConfigFlow
from custom_components.weber_connect.const import CONF_MESSAGE_VERSION, DOMAIN
from custom_components.weber_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.weber_connect.support import (
    SupportEvent,
    SupportJournal,
    bluetooth_summary,
    error_category,
    report_placeholders,
    support_report,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
SECRET = "secret-token-with-private-address@example.com"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, "none"),
        (TimeoutError(), "timeout"),
        (RuntimeError(SECRET), "unclassified_error"),
        ("The hub is not reachable from an active adapter", "no_connectable_adapter"),
        ("Every connection slot is busy", "bluetooth_slots_busy"),
        ("Bluetooth connection could not be established", "bluetooth_connection_failed"),
        ("Bluetooth services changed", "bluetooth_services_unavailable"),
        ("unsupported encrypted response", "unsupported_encrypted_response"),
        ("invalid transport frame", "invalid_transport_frame"),
        ("corrupted protocol envelope", "invalid_protocol_envelope"),
        ("The hub did not confirm pairing", "pairing_confirmation_timeout"),
        ("The hub returned DENIED for pairing", "pairing_rejected"),
        ("invalid credentials", "credentials_rejected"),
        ("HTTP 401", "cloud_unauthorized"),
        ("HTTP 403", "cloud_forbidden"),
        ("TimeoutError: " + SECRET, "timeout"),
        ("request timed out", "timeout"),
        ("Could not reach Weber cloud: " + SECRET, "cloud_unreachable"),
    ],
)
def test_error_categories_do_not_echo_source_text(
    error: Exception | str | None, expected: str
) -> None:
    assert error_category(error) == expected


def test_report_is_bounded_copyable_and_excludes_exception_text() -> None:
    journal = SupportJournal()
    for _ in range(30):
        journal.record(SupportEvent.HANDSHAKE)
    try:
        raise RuntimeError(SECRET)
    except RuntimeError as error:
        journal.record(SupportEvent.FAILED, error)
    report = support_report(stage="pairing_failed", journal=journal)
    assert len(report["events"]) == 12
    placeholders = report_placeholders(report)
    assert SECRET not in str(placeholders)
    assert __file__ not in str(placeholders)
    assert report["events"][-1]["locations"] == []
    assert json.loads(placeholders["report"]) == report
    body = parse_qs(urlsplit(placeholders["report_url"]).query)["body"][0]
    assert json.loads(body.split("```json\n")[1].split("\n```")[0]) == report
    assert len(placeholders["report_url"]) < 8000
    assert report["integration_version"] and report["home_assistant_version"]


@pytest.mark.parametrize("path", ["local", "remote", "unknown"])
def test_bluetooth_summary_excludes_names_addresses_and_payloads(hass: object, path: str) -> None:
    info = SimpleNamespace(address=SECRET, source=SECRET, name=SECRET, rssi=-62)
    scanner = None if path == "unknown" else SimpleNamespace(connector=path == "remote")
    with (
        patch(
            "custom_components.weber_connect.support.bluetooth.async_discovered_service_info",
            return_value=[info],
        ),
        patch(
            "custom_components.weber_connect.support.bluetooth.async_scanner_by_source",
            return_value=scanner,
        ),
    ):
        summary = bluetooth_summary(hass, SECRET)
    assert summary["selected_connectable"] is True
    assert summary["path_kind"] == path
    assert summary["selected_rssi"] == -62
    assert SECRET not in str(summary)


def test_bluetooth_advertisement_only_and_unavailable(hass: object) -> None:
    with patch(
        "custom_components.weber_connect.support.bluetooth.async_discovered_service_info",
        side_effect=[[SimpleNamespace(address=SECRET)], []],
    ):
        summary = bluetooth_summary(hass, SECRET)
    assert summary["selected_advertisement_visible"] is True
    assert summary["selected_connectable"] is False
    assert summary["selected_rssi"] is None
    with patch(
        "custom_components.weber_connect.support.bluetooth.async_discovered_service_info",
        side_effect=KeyError,
    ):
        assert bluetooth_summary(hass, None) == {"available": False}


@pytest.mark.parametrize(
    "stage",
    [
        "no_devices",
        "pairing_failed",
        "cloud_preparation_failed",
        "cloud_not_linked",
        "cloud_unavailable",
        "setup_failed",
    ],
)
async def test_every_failure_offers_report_and_return_preserves_attempt(
    hass: object, stage: str
) -> None:
    flow = WeberConnectConfigFlow()
    flow.hass = hass
    flow.context = {}
    identity = flow._identity = SimpleNamespace(companion_id=SECRET, public_key=SECRET)
    flow._address = SECRET
    flow._pairing_failure_reason = SECRET
    flow._support_journal.record(SupportEvent.REQUESTED)
    flow._support_journal.record(SupportEvent.FAILED, RuntimeError(SECRET))
    menu = await getattr(flow, f"async_step_{stage}")()
    assert "support" in menu["menu_options"]
    result = await flow.async_step_support()
    assert result["type"] is FlowResultType.MENU
    assert SECRET not in str(result["description_placeholders"])
    report = json.loads(result["description_placeholders"]["report"])
    assert report["stage"] == stage
    assert report["events"][-2]["event"] == "pairing_request_sent"
    assert report["pairing_complete"] is False
    returned = await flow.async_step_return_to_error()
    assert returned["step_id"] == stage
    assert flow._identity is identity
    assert flow._pairing_task is None


async def test_unloaded_entry_can_download_report_without_credentials(hass: object) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={"cloud_password": SECRET, CONF_MESSAGE_VERSION: SECRET}
    )
    entry.add_to_hass(hass)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["support_report"]["runtime_loaded"] is False
    assert result["support_report"]["message_version"] is None
    assert SECRET not in str(result)


def test_runtime_report_excludes_names_and_unsafe_metadata() -> None:
    journal = SupportJournal()
    journal.record(SupportEvent.DISCONNECTED, "timeout " + SECRET)
    coordinator = SimpleNamespace(
        data={"software_version": "2.0.3_7398", "hardware_version": SECRET},
        successful_updates=3,
        failed_updates=2,
        consecutive_failures=2,
        last_error=SECRET,
        support_journal=journal,
    )
    entry = SimpleNamespace(
        data={CONF_MESSAGE_VERSION: 11}, runtime_data=SimpleNamespace(coordinator=coordinator)
    )
    report = support_report(stage="settings", entry=entry)
    assert report["software_version"] == "2.0.3_7398"
    assert report["hardware_version"] is None
    assert report["message_version"] == 11
    assert report["last_error"] == "unclassified_error"
    assert report["events"][-1]["error"] == "timeout"
    assert SECRET not in str(report)


async def test_support_navigation_through_home_assistant_flow_manager(hass: object) -> None:
    from unittest.mock import AsyncMock

    with (
        patch("homeassistant.setup._async_process_dependencies", new=AsyncMock(return_value=[])),
        patch(
            "custom_components.weber_connect.config_flow.bluetooth.async_discovered_service_info",
            return_value=[],
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "no_devices"
        flow_id = result["flow_id"]
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": "support"}
        )
        assert result["step_id"] == "support"
        assert "report_url" in result["description_placeholders"]
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": "return_to_error"}
        )
        assert result["step_id"] == "no_devices"
        assert result["flow_id"] == flow_id
        assert not hass.config_entries.async_entries(DOMAIN)
        hass.config_entries.flow.async_abort(flow_id)

"""Allowlisted support reports shared by setup, recovery, and diagnostics.

Never collect log lines, exception messages, packet contents, names, addresses,
URLs, or credentials. Reports contain structured milestones, error categories,
and code locations only. Nothing is sent until the user opens the report link.
"""

from __future__ import annotations

import json
import platform
import re
import time
from collections import deque
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from .const import CONF_MESSAGE_VERSION

INTEGRATION_VERSION: str = json.loads(Path(__file__).with_name("manifest.json").read_text())[
    "version"
]
ISSUE_URL = "https://github.com/ProspectOre/weber-connect-unofficial/issues/new"


class SupportEvent(StrEnum):
    DISCOVERY = "discovery"
    PREPARING = "preparing_cloud_identity"
    CONNECTING = "connecting_bluetooth"
    SERVICES = "bluetooth_services_ready"
    HANDSHAKE = "handshake_sent"
    REQUESTED = "pairing_request_sent"
    CONFIRMED = "pairing_confirmed"
    CLOUD = "checking_cloud_association"
    FAILED = "failed"
    CONNECTED = "receiving_updates"
    DISCONNECTED = "update_failed"


def error_category(error: Exception | str | None) -> str:
    """Map known failure signatures to fixed labels; never return source text."""

    if error is None:
        return "none"
    message = str(error).lower()
    for signature, category in (
        ("not reachable from an active", "no_connectable_adapter"),
        ("connection slot", "bluetooth_slots_busy"),
        ("connection could not be established", "bluetooth_connection_failed"),
        ("bluetooth services", "bluetooth_services_unavailable"),
        ("unsupported encrypted", "unsupported_encrypted_response"),
        ("invalid transport frame", "invalid_transport_frame"),
        ("corrupted protocol", "invalid_protocol_envelope"),
        ("did not confirm pairing", "pairing_confirmation_timeout"),
        ("for pairing", "pairing_rejected"),
        ("credential", "credentials_rejected"),
        ("401", "cloud_unauthorized"),
        ("403", "cloud_forbidden"),
        ("timeout", "timeout"),
        ("timed out", "timeout"),
        ("could not reach weber cloud", "cloud_unreachable"),
    ):
        if signature in message:
            return category
    if isinstance(error, TimeoutError):
        return "timeout"
    return "unclassified_error"


class SupportJournal:
    """Keep a small in-memory history per flow or configured device."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._events: deque[dict[str, Any]] = deque(maxlen=12)

    def record(self, event: SupportEvent, error: Exception | str | None = None) -> None:
        item: dict[str, Any] = {
            "seconds": round(time.monotonic() - self._started, 1),
            "event": event.value,
        }
        if error is not None:
            item["error"] = error_category(error)
            if isinstance(error, Exception):
                # Restrict locations to integration source files. No absolute
                # paths, exception text, locals, or external library frames.
                locations = []
                traceback = error.__traceback__
                while traceback is not None:
                    path = Path(traceback.tb_frame.f_code.co_filename)
                    if path.parent.name == "weber_connect":
                        locations.append(f"{path.name}:{traceback.tb_lineno}")
                    traceback = traceback.tb_next
                item["locations"] = locations[-3:]
        self._events.append(item)

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._events]


def bluetooth_summary(hass: HomeAssistant, address: str | None) -> dict[str, Any]:
    """Describe reachability without exporting scanner names or identifiers."""

    try:
        visible = bluetooth.async_discovered_service_info(hass, connectable=False)
        connectable = bluetooth.async_discovered_service_info(hass, connectable=True)
    except KeyError, RuntimeError:
        return {"available": False}
    selected = next((info for info in connectable if info.address == address), None)
    source = getattr(selected, "source", None)
    scanner = bluetooth.async_scanner_by_source(hass, source) if source else None
    return {
        "available": True,
        "device_selected": address is not None,
        "selected_advertisement_visible": any(info.address == address for info in visible),
        "selected_connectable": selected is not None,
        "selected_rssi": getattr(selected, "rssi", None),
        "path_kind": ("unknown" if scanner is None else "remote" if scanner.connector else "local"),
    }


def _version(value: Any) -> str | None:
    """Only accept short version-shaped metadata, never arbitrary device text."""

    if isinstance(value, str) and re.fullmatch(r"[\w.\-]{1,40}", value, flags=re.ASCII):
        return value
    return None


def support_report(
    *,
    stage: str,
    journal: SupportJournal | None = None,
    entry: ConfigEntry | None = None,
) -> dict[str, Any]:
    """Build an allowlisted report without depending on a loaded runtime."""

    report: dict[str, Any] = {
        "schema": 1,
        "integration_version": INTEGRATION_VERSION,
        "home_assistant_version": HA_VERSION,
        "python_version": platform.python_version(),
        "os": platform.system(),
        "stage": stage,
        "events": journal.snapshot() if journal is not None else [],
    }
    if entry is None:
        return report
    runtime = getattr(entry, "runtime_data", None)
    report["runtime_loaded"] = runtime is not None
    message_version = entry.data.get(CONF_MESSAGE_VERSION)
    report["message_version"] = message_version if type(message_version) is int else None
    if runtime is None:
        return report
    coordinator = runtime.coordinator
    state = coordinator.data or {}
    report.update(
        {
            "software_version": _version(state.get("software_version")),
            "hardware_version": _version(state.get("hardware_version")),
            "successful_updates": coordinator.successful_updates,
            "failed_updates": coordinator.failed_updates,
            "consecutive_failures": coordinator.consecutive_failures,
            "last_error": error_category(coordinator.last_error),
            "events": coordinator.support_journal.snapshot(),
        }
    )
    return report


def report_placeholders(report: dict[str, Any]) -> dict[str, str]:
    """Provide a readable copy and a prefilled issue; do not submit anything."""

    body = (
        "### What happened?\nPlease describe the problem and what you expected.\n\n"
        "### Device and connection\n"
        "- Exact grill/thermometer model:\n- Bluetooth adapter/proxy model:\n"
        "- Does Weber Connect work with phone Bluetooth off?\n"
        "- For pairing: what appears on the thermometer, and how does the official app pair?\n\n"
        "### Support report\n```json\n" + json.dumps(report, separators=(",", ":")) + "\n```\n"
    )
    return {
        "report": json.dumps(report, indent=2),
        "report_url": ISSUE_URL
        + "?"
        + urlencode({"title": "Weber Connect support report", "body": body}),
    }

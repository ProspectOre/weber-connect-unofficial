"""Behavioral regressions reconstructed from sanitized field reports."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.weber_connect.entity import WeberEntity
from custom_components.weber_connect.state import normalize_state

FIXTURE_PATH = Path(__file__).parent / "fixtures/field_reports/smokefire_manual_cook.json"
ROOT_KEYS = {
    "schema_version",
    "source_url",
    "evidence_kind",
    "device",
    "reported_manual_cook",
    "synthetic_controls",
}
DEVICE_KEYS = {
    "product_family",
    "model",
    "integration_release",
    "firmware",
    "home_assistant_version",
}
STATUS_KEYS = {"device_state", "cook_mode", "probes", "timed_sessions", "timers", "burners"}
ROW_KEYS = {"probe_number", "state"}
REPORTED_KEYS = {"model_title", "raw_status", "expected_cooking"}
CONTROL_KEYS = {"name", "raw_status", "expected_cooking"}
PRIVATE_KEY_PARTS = (
    "address",
    "token",
    "secret",
    "credential",
    "account",
    "device_id",
    "raw_byte",
    "packet_byte",
    "wire_byte",
    "payload",
    "packet",
    "bytes",
    "nickname",
    "display_name",
    "serial_number",
    "food",
)
PRIVATE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\b(?:access[_ -]?token|password|private[_ -]?key)\s*[:=]"),
    re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b"),
    re.compile(r"(?i)\b(?:[0-9a-f]{12}|[0-9a-f]{32,})\b"),
    re.compile(r"(?<!\w)(?:[0-9a-f]{2}){8,}(?!\w)", re.IGNORECASE),
)


def _require_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unknown or missing fields")


def _assert_private_data_absent(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = key.casefold().replace("-", "_")
            if any(part in normalized_key for part in PRIVATE_KEY_PARTS):
                raise ValueError(f"private or raw field is not allowed: {key}")
            _assert_private_data_absent(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_private_data_absent(nested)
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in PRIVATE_VALUE_PATTERNS
    ):
        raise ValueError("private identifier, credential, or raw bytes are not allowed")


def _validate_fixture(report: dict[str, Any]) -> None:
    """Validate the small allowlisted fixture shape before using its behavior."""

    _require_keys(report, ROOT_KEYS, "fixture")
    if report["schema_version"] != 1:
        raise ValueError("unsupported field-report fixture version")
    if report["evidence_kind"] != "reconstructed_from_report":
        raise ValueError("reported observation must retain its reconstruction label")
    if not report["source_url"].startswith("https://github.com/"):
        raise ValueError("field-report source must be a GitHub URL")

    device = report["device"]
    _require_keys(device, DEVICE_KEYS, "device")
    _require_keys(report["reported_manual_cook"], REPORTED_KEYS, "reported event")
    for case in [report["reported_manual_cook"], *report["synthetic_controls"]]:
        _require_keys(case["raw_status"], STATUS_KEYS, "raw_status")
        if not isinstance(case["raw_status"]["probes"], list):
            raise ValueError("probes must be a list")
        for row in case["raw_status"]["probes"]:
            _require_keys(row, ROW_KEYS, "probe")
    for control in report["synthetic_controls"]:
        _require_keys(control, CONTROL_KEYS, "synthetic control")

    _assert_private_data_absent(report)


@pytest.fixture(scope="module")
def smokefire_report() -> dict[str, Any]:
    report = json.loads(FIXTURE_PATH.read_text())
    _validate_fixture(report)
    return report


def test_reconstructed_manual_cook_report_normalizes_as_cooking(
    smokefire_report: dict[str, Any],
) -> None:
    report = smokefire_report["reported_manual_cook"]
    state = normalize_state(report["raw_status"], source="cloud", connected=True)
    assert state["cooking"] is report["expected_cooking"] is True


def test_synthetic_non_cook_controls_do_not_claim_cooking(
    smokefire_report: dict[str, Any],
) -> None:
    for control in smokefire_report["synthetic_controls"]:
        state = normalize_state(control["raw_status"], source="cloud", connected=True)
        assert state["cooking"] is control["expected_cooking"] is False


def test_report_model_title_labels_device_as_smokefire(
    smokefire_report: dict[str, Any],
) -> None:
    coordinator = SimpleNamespace(
        data={}, last_update_success=True, async_add_listener=MagicMock(return_value=MagicMock())
    )
    report = smokefire_report["reported_manual_cook"]
    entry = SimpleNamespace(
        unique_id="fixture-hub",
        entry_id="fixture-entry",
        title=report["model_title"],
        data={"address": "00:00:00:00:00:00", "model_id": 2},
    )
    entity = WeberEntity(coordinator, entry, "grill_temperature")  # type: ignore[arg-type]
    assert entity._attr_device_info["model"] == "SmokeFire"


def test_field_report_fixture_allowlist_and_privacy_guard_reject_changes(
    smokefire_report: dict[str, Any],
) -> None:
    invalid_field = copy.deepcopy(smokefire_report)
    invalid_field["reported_manual_cook"]["raw_status"]["payload_hex"] = "0102030405060708"
    with pytest.raises(ValueError, match="unknown or missing fields"):
        _validate_fixture(invalid_field)

    private_identifier = copy.deepcopy(smokefire_report)
    private_identifier["device"]["address"] = "AA:BB:CC:DD:EE:FF"
    with pytest.raises(ValueError, match="unknown or missing fields"):
        _validate_fixture(private_identifier)

    private_value = copy.deepcopy(smokefire_report)
    private_value["source_url"] = "https://github.com/example/repo?access_token=secret"
    with pytest.raises(ValueError, match="private identifier"):
        _validate_fixture(private_value)


@pytest.mark.parametrize(
    "nested_private_data",
    [
        {"nested": {"address": "AA:BB:CC:DD:EE:FF"}},
        {"nested": {"token": "secret"}},
        {"nested": {"raw_bytes": "0102030405060708"}},
        {"nested": {"payload": "0102030405060708"}},
        {"nested": {"value": "00112233445566778899aabbccddeeff"}},
    ],
)
def test_recursive_privacy_guard_rejects_nested_private_data(
    nested_private_data: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        _assert_private_data_absent(nested_private_data)

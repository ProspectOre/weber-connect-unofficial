"""Runtime changes and incomplete physical evidence must block a stable release."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import validate_release as release


def evidence() -> tuple[dict, dict]:
    fingerprint = release.runtime_fingerprint()
    physical = {
        "runtime_sha256": fingerprint,
        "verified": dict.fromkeys(release.PHYSICAL_ACCEPTANCE_GATES, True),
        "endurance": {
            "duration_seconds": 3600,
            "maximum_update_gap_seconds": 12,
            "manual_recoveries": 0,
            "unexpected_entry_reloads": 0,
            "capture_errors": 0,
            "new_failed_updates": 0,
            "disconnected_samples": 0,
            "samples": 360,
            "candidate_unchanged": True,
            "final_connected": True,
        },
    }
    return physical, {"runtime_sha256": fingerprint}


def test_complete_evidence_accepts_current_runtime() -> None:
    release.check_runtime_acceptance(*evidence())


@pytest.mark.parametrize("which", [0, 1])
def test_other_runtime_evidence_cannot_authorize_release(which: int) -> None:
    records = list(evidence())
    records[which]["runtime_sha256"] = "stale"
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(*records)


@pytest.mark.parametrize("gate", release.PHYSICAL_ACCEPTANCE_GATES)
@pytest.mark.parametrize("value", [False, "true", None])
def test_each_physical_gate_requires_explicit_success(gate: str, value: object) -> None:
    physical, automated = evidence()
    physical["verified"][gate] = value
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("duration_seconds", 3599),
        ("duration_seconds", float("nan")),
        ("maximum_update_gap_seconds", 31),
        ("maximum_update_gap_seconds", float("inf")),
        ("samples", 1),
        ("samples", 359),
        ("samples", True),
        ("new_failed_updates", 99),
        ("disconnected_samples", 99),
        ("manual_recoveries", 1),
        ("unexpected_entry_reloads", 1),
        ("capture_errors", 1),
        ("capture_errors", False),
        ("candidate_unchanged", False),
        ("final_connected", False),
    ],
)
def test_inadequate_endurance_run_blocks_release(key: str, value: object) -> None:
    physical, automated = evidence()
    physical["endurance"][key] = value
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


def test_version_label_only_change_preserves_runtime_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "INTEGRATION", tmp_path)
    manifest = {"version": "3.1.2", "requirements": ["example==1"]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    original = release.runtime_fingerprint()
    manifest["version"] = "3.2.0"
    path.write_text(json.dumps(manifest))
    assert release.runtime_fingerprint() == original
    changed = copy.deepcopy(manifest)
    changed["requirements"] = ["example==2"]
    path.write_text(json.dumps(changed))
    assert release.runtime_fingerprint() != original
    path.write_text(json.dumps(manifest))
    (tmp_path / "coordinator.py").write_text("# runtime changed\n")
    assert release.runtime_fingerprint() != original


def test_future_release_still_checks_runtime_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "VERSION", "3.3.0")

    def reject(*args: object) -> None:
        raise RuntimeError("acceptance checked")

    monkeypatch.setattr(release, "check_runtime_acceptance", reject)
    with pytest.raises(RuntimeError, match="acceptance checked"):
        release.check_privacy_and_scope()


@pytest.mark.parametrize("key", ["new_failed_updates", "disconnected_samples", "samples"])
@pytest.mark.parametrize("value", [None, False, -1, "360", 360.0])
def test_endurance_counters_require_valid_integers(key: str, value: object) -> None:
    physical, automated = evidence()
    physical["endurance"][key] = value
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)

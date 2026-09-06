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


def amended_evidence() -> tuple[dict, dict]:
    physical, automated = evidence()
    (version, baseline, current), amendment_type = next(
        iter(release.PHYSICAL_EVIDENCE_AMENDMENTS.items())
    )
    assert version == "3.2.0"
    physical["runtime_sha256"] = baseline
    physical["runtime_amendment"] = {
        "type": amendment_type,
        "baseline_runtime_sha256": baseline,
        "runtime_sha256": current,
        "verified": dict.fromkeys(release.PHYSICAL_AMENDMENT_GATES, True),
    }
    automated["runtime_sha256"] = current
    return physical, automated


def test_exact_reviewed_amendment_preserves_original_endurance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical, automated = amended_evidence()
    original = copy.deepcopy(physical)
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    release.check_runtime_acceptance(physical, automated)
    assert physical == original
    assert physical["runtime_sha256"] != automated["runtime_sha256"]


@pytest.mark.parametrize("field", ["type", "baseline_runtime_sha256", "runtime_sha256"])
def test_amendment_rejects_wrong_transition_fields(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical, automated = amended_evidence()
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    physical["runtime_amendment"][field] = "unreviewed"
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


@pytest.mark.parametrize("gate", release.PHYSICAL_AMENDMENT_GATES)
@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_amendment_requires_each_fresh_physical_gate(
    gate: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical, automated = amended_evidence()
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    physical["runtime_amendment"]["verified"][gate] = value
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


@pytest.mark.parametrize("value", [None, [], "approved", {}])
def test_amendment_requires_structured_record(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical, automated = amended_evidence()
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    physical["runtime_amendment"] = value
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


def test_prior_fingerprint_without_amendment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    physical, automated = amended_evidence()
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    del physical["runtime_amendment"]
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


@pytest.mark.parametrize("change", ["baseline", "current", "release", "automated", "relabel"])
def test_amendment_cannot_authorize_another_runtime_or_release(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    physical, automated = amended_evidence()
    current = automated["runtime_sha256"]
    if change == "baseline":
        physical["runtime_sha256"] = "another-baseline"
        physical["runtime_amendment"]["baseline_runtime_sha256"] = "another-baseline"
    elif change == "current":
        current = "another-runtime"
        automated["runtime_sha256"] = current
        physical["runtime_amendment"]["runtime_sha256"] = current
    elif change == "release":
        monkeypatch.setattr(release, "VERSION", "3.3.0")
    elif change == "automated":
        automated["runtime_sha256"] = physical["runtime_sha256"]
    else:
        physical["runtime_sha256"] = current
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: current)
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


def test_amendment_still_requires_complete_original_endurance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical, automated = amended_evidence()
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    physical["endurance"]["duration_seconds"] = 3599
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)


def test_amendment_missing_gate_cannot_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    physical, automated = amended_evidence()
    monkeypatch.setattr(release, "runtime_fingerprint", lambda: automated["runtime_sha256"])
    del physical["runtime_amendment"]["verified"]["fresh_telemetry"]
    with pytest.raises(SystemExit):
        release.check_runtime_acceptance(physical, automated)

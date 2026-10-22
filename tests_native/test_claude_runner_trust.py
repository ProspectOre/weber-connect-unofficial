"""Regression coverage for candidate-runner trust gates."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PULL_REQUEST_EVENTS = {"opened", "synchronize", "reopened", "edited"}
EDIT_ONLY = "github.event.action == 'edited' && !github.event.changes.base"
GITHUB_HOSTED_RUNNER = re.compile(
    r"ubuntu-(?:latest|\d{2}\.\d{2})(?:-arm)?"
    r"|windows-(?:latest|\d{4})(?:-arm)?"
    r"|macos-(?:latest|\d{2})(?:-intel|-large|-xlarge)?"
)


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _pull_request_events(workflow: str) -> set[str]:
    match = re.search(r"pull_request:\s+types: \[([^]]+)\]", workflow)
    assert match is not None
    return {event.strip() for event in match.group(1).split(",")}


def _author_gates(workflow: str) -> list[str]:
    """Conditions that admit or skip a run by who opened or pushed it."""
    return re.findall(
        r"author_association|pull_request\.user\.|sender\.type|dependabot\[bot\]", workflow
    )


def test_ci_runs_every_pull_request_on_safe_runners() -> None:
    workflow = _workflow("ci.yml")
    jobs = yaml.safe_load(workflow)["jobs"]

    assert _pull_request_events(workflow) == PULL_REQUEST_EVENTS
    runners = re.findall(r"^\s+runs-on: (.+)$", workflow, re.MULTILINE)
    assert runners and set(runners) == {"ubuntu-latest"}
    assert "self-hosted" not in workflow
    assert "runner.os" not in workflow
    # Every PR, bot or person, same repository or fork, runs the same gates.
    assert _author_gates(workflow) == []
    assert "draft" not in workflow
    # An edit changes no code: only the heavy lanes skip, and the required
    # check reports the last code run for the head instead of passing.
    for name in ("matrix", "hassfest", "hacs"):
        assert jobs[name]["if"] == f"${{{{ !({EDIT_ONLY}) }}}}"
    assert jobs["attribution"]["if"] == "github.event_name == 'pull_request'"
    assert jobs["ci"]["if"] == "always()"
    assert set(jobs["ci"]["needs"]) == {
        "attribution",
        "matrix",
        "validate",
        "latest-smoke",
        "hassfest",
        "hacs",
    }
    assert ".github/scripts/last-code-run.sh ci" in workflow


def test_retired_review_gate_is_not_restored() -> None:
    assert not (ROOT / ".github" / "review-gate").exists()
    for name in ("review-gate.yml", "auto-merge.yml", "claude-review.yml"):
        assert not (ROOT / ".github" / "workflows" / name).exists()


def test_codeql_admits_fork_analysis_without_fork_result_upload() -> None:
    workflow = _workflow("codeql-analysis.yml")

    assert _pull_request_events(workflow) == {"opened", "synchronize", "reopened"}
    assert _author_gates(workflow) == []
    assert re.findall(r"^\s+runs-on: (.+)$", workflow, re.MULTILINE) == ["ubuntu-latest"]
    assert "self-hosted" not in workflow
    assert "upload: ${{ github.event_name != 'pull_request'" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository }}" in workflow
    assert "contents: read" in workflow
    assert "security-events: write" in workflow


def _runner_violations(path: Path) -> list[str]:
    """Return every job whose runner is not a literal GitHub-hosted label.

    This public repository has no self-hosted runner; a job targeting one would
    queue forever or, if a runner were registered again, run fork code privately.
    """
    text = path.read_text(encoding="utf-8")
    violations = [f"{path.name}: mentions self-hosted"] if "self-hosted" in text else []
    jobs = (yaml.safe_load(text) or {}).get("jobs") or {}
    if not jobs:
        violations.append(f"{path.name}: defines no jobs")
    for name, job in jobs.items():
        runs_on = job.get("runs-on") if isinstance(job, dict) else None
        if not isinstance(runs_on, str) or not GITHUB_HOSTED_RUNNER.fullmatch(runs_on):
            violations.append(f"{path.name}:{name}: runs-on {runs_on!r}")
    return violations


def test_every_workflow_job_runs_on_a_github_hosted_runner() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflows
    assert [v for path in workflows for v in _runner_violations(path)] == []


@pytest.mark.parametrize(
    "jobs",
    [
        "  build:\n    runs-on: [self-hosted, macOS, ARM64, weber]\n",
        "  build:\n    runs-on: self-hosted\n",
        "  build:\n    runs-on: mbp\n",
        "  build:\n    runs-on: ${{ matrix.os }}\n",
        "  build:\n    runs-on:\n      group: private\n",
        "  build:\n    uses: ./.github/workflows/reusable.yml\n",
        "  build:\n    runs-on: ubuntu-latest  # not self-hosted\n",
        "  build:\n    runs-on: ubuntu-mine\n",
    ],
)
def test_runner_check_rejects_runners_that_are_not_github_hosted(tmp_path: Path, jobs: str) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(f"on: push\njobs:\n{jobs}", encoding="utf-8")
    assert _runner_violations(path)


@pytest.mark.parametrize(
    "runner", ["ubuntu-latest", "ubuntu-24.04", "ubuntu-24.04-arm", "windows-2025", "macos-15"]
)
def test_runner_check_accepts_github_hosted_labels(tmp_path: Path, runner: str) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(f"on: push\njobs:\n  build:\n    runs-on: {runner}\n", encoding="utf-8")
    assert _runner_violations(path) == []

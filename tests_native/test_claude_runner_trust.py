"""Regression coverage for candidate-runner trust gates."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_LIFECYCLE_EVENTS = {"opened", "ready_for_review", "synchronize"}
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
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


def _ci_admitted(
    *,
    event: str,
    draft: bool = False,
    same_repository: bool = True,
    author_login: str = "contributor",
    author_type: str = "User",
    sender_type: str = "User",
    author_association: str = "CONTRIBUTOR",
) -> bool:
    if event != "pull_request":
        return True
    return not draft and (
        author_login == "dependabot[bot]"
        or (
            author_type != "Bot"
            and sender_type != "Bot"
            and (not same_repository or author_association in TRUSTED_ASSOCIATIONS)
        )
    )


def test_ci_admits_same_repo_fork_and_default_branch_on_safe_runners() -> None:
    workflow = _workflow("ci.yml")

    assert _pull_request_events(workflow) == TRUSTED_LIFECYCLE_EVENTS
    runners = re.findall(r"^\s+runs-on: (.+)$", workflow, re.MULTILINE)
    assert runners and set(runners) == {"ubuntu-latest"}
    assert "self-hosted" not in workflow
    assert "runner.os" not in workflow
    assert "github.event.pull_request.head.repo.full_name != github.repository" in workflow
    assert _ci_admitted(event="pull_request", same_repository=True, author_association="MEMBER")
    assert _ci_admitted(event="pull_request", same_repository=False)
    assert _ci_admitted(event="push")
    assert not _ci_admitted(event="pull_request", draft=True, same_repository=False)
    assert not _ci_admitted(event="pull_request", author_type="Bot")
    assert not _ci_admitted(event="pull_request", sender_type="Bot")


def test_retired_review_gate_is_not_restored() -> None:
    assert not (ROOT / ".github" / "review-gate").exists()
    for name in ("review-gate.yml", "auto-merge.yml", "claude-review.yml"):
        assert not (ROOT / ".github" / "workflows" / name).exists()


def test_codeql_admits_fork_analysis_without_fork_result_upload() -> None:
    workflow = _workflow("codeql-analysis.yml")

    assert _pull_request_events(workflow) == {"synchronize"}
    assert "github.event.pull_request.head.repo.full_name != github.repository" in workflow
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

"""Regression coverage for candidate-runner and Claude instruction trust gates."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_LIFECYCLE_EVENTS = {"opened", "ready_for_review", "synchronize"}


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _workflow_doc(name: str) -> dict:
    # BaseLoader preserves GitHub's `on` key as a string instead of YAML 1.1 bool.
    return yaml.load(_workflow(name), Loader=yaml.BaseLoader)


def _pull_request_events(workflow: str) -> set[str]:
    match = re.search(r"pull_request:\s+types: \[([^]]+)\]", workflow)
    assert match is not None
    return {event.strip() for event in match.group(1).split(",")}


def test_ci_workflow_admission_permissions_and_checkout_are_restricted() -> None:
    workflow = _workflow_doc("ci.yml")

    assert set(workflow["on"]["pull_request"]["types"]) == TRUSTED_LIFECYCLE_EVENTS
    assert workflow["permissions"] == {"contents": "read"}
    assert {job["runs-on"] for job in workflow["jobs"].values()} == {"ubuntu-latest"}
    assert all("permissions" not in job for job in workflow["jobs"].values())

    expected_admission_terms = (
        "github.event.pull_request.draft == false",
        "github.event.pull_request.user.login == 'dependabot[bot]'",
        "github.event.pull_request.user.type != 'Bot'",
        "github.event.sender.type != 'Bot'",
        "github.event.pull_request.head.repo.full_name != github.repository",
        "github.event.pull_request.author_association",
        "OWNER",
        "MEMBER",
        "COLLABORATOR",
    )
    for job_name in ("validate", "hassfest", "hacs"):
        admission = workflow["jobs"][job_name]["if"]
        for term in expected_admission_terms:
            assert term in admission

        for step in workflow["jobs"][job_name]["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] == "false"

    aggregate = workflow["jobs"]["ci"]
    assert aggregate["if"] == "always()"
    assert set(aggregate["needs"]) == {"validate", "hassfest", "hacs"}
    assert all("uses" not in step for step in aggregate["steps"])
    assert len(aggregate["steps"]) == 1
    gate = aggregate["steps"][0]
    assert set(gate["env"]) == {"VALIDATE", "HASSFEST", "HACS"}
    assert all(
        gate["env"][key] == f"${{{{ needs.{job}.result }}}}"
        for key, job in (
            ("VALIDATE", "validate"),
            ("HASSFEST", "hassfest"),
            ("HACS", "hacs"),
        )
    )
    for job in ("VALIDATE", "HASSFEST", "HACS"):
        assert f'test "${job}" = success' in gate["run"]


def test_claude_preflight_bootstraps_brew_path_before_gh() -> None:
    workflow = _workflow("claude.yml")
    path_setup = 'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"'
    assert path_setup in workflow
    assert "command -v gh >/dev/null 2>&1" in workflow
    assert workflow.index(path_setup) < workflow.index('gh api "repos/$REPOSITORY')


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


def test_claude_issue_instructions_are_opened_by_author_only() -> None:
    workflow = _workflow("claude.yml")

    assert "types: [opened]" in workflow
    assert "assigned" not in workflow
    assert "ACTOR_LOGIN: ${{ github.actor }}" in workflow
    assert "ISSUE_NUMBER: ${{ github.event.issue.number }}" in workflow
    assert "gh api \"repos/$REPOSITORY/issues/$ISSUE_NUMBER\" --jq '.user.login'" in workflow
    assert '[[ "$issue_author" == "$ACTOR_LOGIN" ]]' in workflow
    assert "trusted=true" in workflow


@pytest.mark.parametrize(
    ("permission", "issue_author", "api_failure", "expected"),
    [
        ("write", "maintainer", "none", "true"),
        ("admin", "maintainer", "none", "true"),
        ("write", "other-author", "none", "false"),
        ("read", "maintainer", "none", "false"),
        ("write", "maintainer", "permission", "false"),
        ("write", "maintainer", "issue", "false"),
    ],
)
def test_actual_claude_issue_preflight_binds_author_and_refuses_api_failures(
    tmp_path, permission, issue_author, api_failure, expected
):
    """Execute the shipping authorization shell with an isolated API substitute."""
    workflow = yaml.safe_load(_workflow("claude.yml"))
    preflight = workflow["jobs"]["claude"]["steps"][0]["run"]
    output = tmp_path / "output"
    # The shell function wins over PATH, so this test cannot contact GitHub.
    substitute = r"""
gh() {
  [[ "$1" == "api" ]] || return 1
  case "$2" in
    */collaborators/*/permission)
      [[ "$TEST_API_FAILURE" != "permission" ]] || return 1
      printf '%s\n' "$TEST_PERMISSION" ;;
    */issues/42)
      [[ "$TEST_API_FAILURE" != "issue" ]] || return 1
      printf '%s\n' "$TEST_ISSUE_AUTHOR" ;;
    *) return 1 ;;
  esac
}
"""
    result = subprocess.run(
        ["bash", "-c", substitute + preflight],
        env={
            **os.environ,
            "GH_TOKEN": "unused-test-token",
            "EVENT_NAME": "issues",
            "ACTOR_LOGIN": "maintainer",
            "ISSUE_NUMBER": "42",
            "PR_URL": "",
            "REPOSITORY": "test/repository",
            "GITHUB_OUTPUT": str(output),
            "TEST_PERMISSION": permission,
            "TEST_ISSUE_AUTHOR": issue_author,
            "TEST_API_FAILURE": api_failure,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert output.read_text().strip() == f"trusted={expected}"


def test_claude_responder_isolated_on_trusted_default_branch_events() -> None:
    workflow = _workflow("claude.yml")
    triggers = workflow.split("on:\n", 1)[1].split("\njobs:", 1)[0]

    assert "issue_comment:" in triggers
    assert "issues:" in triggers
    assert "pull_request_review_comment:" not in triggers
    assert "pull_request_review:" not in triggers
    assert "pull_request_target:" not in triggers
    assert "pull_request:" not in triggers
    assert re.findall(r"^\s+runs-on: (.+)$", workflow, re.MULTILINE) == ["ubuntu-latest"]
    checkout = workflow.split("- name: Checkout repository", 1)[1].split(
        "- name: Run Claude Code", 1
    )[0]
    assert "ref: ${{ github.sha }}" in checkout
    assert "persist-credentials: false" in checkout

"""Regression coverage for candidate-runner and Claude instruction trust gates."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_LIFECYCLE_EVENTS = {"opened", "ready_for_review", "synchronize"}
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


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
    assert workflow.count("github.event_name == 'pull_request' && 'ubuntu-latest'") == 4
    assert workflow.count('fromJSON(\'["self-hosted","macOS","ARM64","weber","mbp"]\')') == 4
    assert "github.event.pull_request.head.repo.full_name != github.repository" in workflow
    assert _ci_admitted(event="pull_request", same_repository=True, author_association="MEMBER")
    assert _ci_admitted(event="pull_request", same_repository=False)
    assert _ci_admitted(event="push")
    assert not _ci_admitted(event="pull_request", draft=True, same_repository=False)
    assert not _ci_admitted(event="pull_request", author_type="Bot")
    assert not _ci_admitted(event="pull_request", sender_type="Bot")


def test_codeql_admits_fork_analysis_without_fork_result_upload() -> None:
    workflow = _workflow("codeql-analysis.yml")

    assert _pull_request_events(workflow) == {"synchronize"}
    assert "github.event.pull_request.head.repo.full_name != github.repository" in workflow
    assert "github.event_name == 'pull_request' && 'ubuntu-latest'" in workflow
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

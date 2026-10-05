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


def _trusted_lifecycle_routes(
    workflow: str,
    *,
    event: str,
    draft: bool = False,
    same_repository: bool = True,
    author_type: str = "User",
    sender_type: str = "User",
    author_association: str = "OWNER",
) -> bool:
    return (
        event in _pull_request_events(workflow)
        and not draft
        and same_repository
        and author_type != "Bot"
        and sender_type != "Bot"
        and author_association in TRUSTED_ASSOCIATIONS
    )


def test_candidate_ci_stays_on_local_runner_for_default_branch_only() -> None:
    workflow = _workflow("ci.yml")

    assert _pull_request_events(workflow) == TRUSTED_LIFECYCLE_EVENTS
    assert workflow.count("github.event_name == 'pull_request' && 'ubuntu-latest'") == 4
    assert "\n  push:\n" in workflow
    assert workflow.count('fromJSON(\'["self-hosted","macOS","ARM64","weber","mbp"]\')') == 4
    for association in TRUSTED_ASSOCIATIONS:
        assert _trusted_lifecycle_routes(
            workflow, event="synchronize", author_association=association
        )
    assert not _trusted_lifecycle_routes(workflow, event="synchronize", same_repository=False)
    assert not _trusted_lifecycle_routes(workflow, event="synchronize", author_type="Bot")
    assert not _trusted_lifecycle_routes(
        workflow, event="synchronize", author_association="CONTRIBUTOR"
    )


def test_codeql_candidate_scan_uses_ephemeral_hosted_runner() -> None:
    workflow = _workflow("codeql-analysis.yml")

    assert _pull_request_events(workflow) == {"synchronize"}
    assert "github.event_name == 'pull_request' && 'ubuntu-latest'" in workflow
    assert 'fromJSON(\'["self-hosted","macOS","ARM64","weber","mbp"]\')' in workflow


def test_claude_issue_instructions_are_opened_by_author_only() -> None:
    workflow = _workflow("claude.yml")

    assert "types: [opened]" in workflow
    assert "assigned" not in workflow
    assert "ACTOR_LOGIN: ${{ github.actor }}" in workflow
    assert "ISSUE_NUMBER: ${{ github.event.issue.number }}" in workflow
    assert "gh api \"repos/$REPOSITORY/issues/$ISSUE_NUMBER\" --jq '.user.login'" in workflow
    assert '[[ "$issue_author" == "$ACTOR_LOGIN" ]]' in workflow
    assert "trusted=true" in workflow

"""Regression coverage for local-runner PR trust gates."""

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


def test_trusted_pr_issue_comments_use_metadata_preflight() -> None:
    workflow = (ROOT / ".github" / "workflows" / "claude.yml").read_text(encoding="utf-8")

    assert "github.event.issue.pull_request == null" not in workflow
    assert "Authorize the event target" in workflow
    assert "ACTOR_LOGIN: ${{ github.actor }}" in workflow
    assert "collaborators/$ACTOR_LOGIN/permission" in workflow
    assert '"$actor_permission" != "maintain"' in workflow
    assert '"$actor_permission" != "push"' in workflow
    assert '"$actor_permission" != "write"' in workflow
    assert ".head.repo.full_name == $repository" in workflow
    assert '.user.type != "Bot"' in workflow
    assert '.author_association == "OWNER"' in workflow
    assert workflow.count("if: steps.target.outputs.trusted == 'true'") == 2


def test_claude_preflight_bootstraps_brew_path_before_gh() -> None:
    workflow = _workflow("claude.yml")

    path_setup = 'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"'
    assert path_setup in workflow
    assert "command -v gh >/dev/null 2>&1" in workflow
    assert workflow.index(path_setup) < workflow.index('gh api "repos/$REPOSITORY')


def test_ci_and_review_lifecycle_keep_every_trust_guard() -> None:
    for name, expected_count in (("ci.yml", 4), ("claude-review.yml", 1)):
        workflow = _workflow(name)

        assert _pull_request_events(workflow) == TRUSTED_LIFECYCLE_EVENTS
        assert workflow.count("github.event.pull_request.draft == false") == expected_count
        assert (
            workflow.count("github.event.pull_request.head.repo.full_name == github.repository")
            == expected_count
        )
        assert workflow.count("github.event.pull_request.user.type != 'Bot'") == expected_count
        assert workflow.count("github.event.sender.type != 'Bot'") == expected_count
        assert (
            workflow.count('contains(fromJSON(\'["OWNER","MEMBER","COLLABORATOR"]\')')
            == expected_count
        )
        assert "reopened" not in workflow


def test_opened_ready_pr_with_existing_commits_routes_ci_and_review() -> None:
    for name in ("ci.yml", "claude-review.yml"):
        assert _trusted_lifecycle_routes(_workflow(name), event="opened")


def test_ready_for_review_and_synchronize_route_ci_and_review() -> None:
    for name in ("ci.yml", "claude-review.yml"):
        workflow = _workflow(name)
        assert _trusted_lifecycle_routes(workflow, event="ready_for_review")
        assert _trusted_lifecycle_routes(workflow, event="synchronize")


def test_same_repository_trusted_associations_route_head_code() -> None:
    for name in ("ci.yml", "claude-review.yml"):
        workflow = _workflow(name)
        for association in TRUSTED_ASSOCIATIONS:
            assert _trusted_lifecycle_routes(
                workflow,
                event="synchronize",
                same_repository=True,
                author_association=association,
            )


def test_draft_and_non_ready_prs_do_not_route_head_code() -> None:
    for name in ("ci.yml", "claude-review.yml"):
        workflow = _workflow(name)
        assert not _trusted_lifecycle_routes(workflow, event="opened", draft=True)
        assert not _trusted_lifecycle_routes(workflow, event="reopened")


def test_fork_bot_and_untrusted_prs_do_not_route_head_code() -> None:
    for name in ("ci.yml", "claude-review.yml"):
        workflow = _workflow(name)
        assert not _trusted_lifecycle_routes(workflow, event="synchronize", same_repository=False)
        assert not _trusted_lifecycle_routes(workflow, event="synchronize", author_type="Bot")
        assert not _trusted_lifecycle_routes(workflow, event="synchronize", sender_type="Bot")
        assert not _trusted_lifecycle_routes(
            workflow, event="synchronize", author_association="CONTRIBUTOR"
        )


def test_codeql_remains_trusted_synchronize_only() -> None:
    workflow = _workflow("codeql-analysis.yml")

    assert _pull_request_events(workflow) == {"synchronize"}
    assert "github.event.sender.type != 'Bot'" in workflow
    assert "github.event.pull_request.user.type != 'Bot'" in workflow


def test_macos_ci_uses_runner_local_python_environment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "if: runner.os == 'Linux'" in workflow
    assert 'python3 -m venv "$RUNNER_TEMP/weber-venv"' in workflow
    assert 'echo "$RUNNER_TEMP/weber-venv/bin" >> "$GITHUB_PATH"' in workflow


def test_macos_validators_are_pinned_and_do_not_require_docker() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "docker run" not in workflow
    assert "759e4658f40b3ccb671d418b8a0ed95224bf4561" in workflow
    assert "3249355704d1a716e637d4d044b6cb4ae72dc271" in workflow
    assert "e6b196171fbcb3cb3eced2c48e789f3dc946b59f7490487df16d8d4e47a85fc4" in workflow
    assert workflow.count("8 * 1024 * 1024") == 3
    assert workflow.count("6 * 1024 * 1024") == 2

"""Regression coverage for local-runner PR trust gates."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trusted_pr_issue_comments_use_metadata_preflight() -> None:
    workflow = (ROOT / ".github" / "workflows" / "claude.yml").read_text(encoding="utf-8")

    assert "github.event.issue.pull_request == null" not in workflow
    assert "Authorize the event target" in workflow
    assert ".head.repo.full_name == $repository" in workflow
    assert '.user.type != "Bot"' in workflow
    assert '.author_association == "OWNER"' in workflow
    assert workflow.count("if: steps.target.outputs.trusted == 'true'") == 2


def test_head_code_runs_only_for_human_synchronize_events() -> None:
    for name in ("ci.yml", "codeql-analysis.yml", "claude-review.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

        assert re.search(r"pull_request:\s+types: \[synchronize\]", workflow)
        assert "github.event.sender.type != 'Bot'" in workflow
        assert "github.event.pull_request.user.type != 'Bot'" in workflow
        assert "opened" not in workflow
        assert "reopened" not in workflow
        assert "ready_for_review" not in workflow


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

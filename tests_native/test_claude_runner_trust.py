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

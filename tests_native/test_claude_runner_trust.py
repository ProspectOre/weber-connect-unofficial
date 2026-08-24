"""Regression coverage for local-runner PR trust gates."""

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

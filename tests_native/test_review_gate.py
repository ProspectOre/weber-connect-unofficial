"""Consumer contracts for the rendered canonical review gate adapter."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "review-gate.yml"


def test_rendered_adapter_binds_review_policy_to_published_source_hashes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "EXPECTED_SHA256:" in workflow
    assert "contents/.github/review-gate/evaluate.sh?ref=$POLICY_REF" in workflow
    assert "contents/.github/review-gate/classify_dependencies.py?ref=$POLICY_REF" in workflow
    assert '[[ "${actual%% *}" == "$EXPECTED_SHA256" ]] || exit 1' in workflow


def test_fork_gate_requires_ci_for_exact_head_and_base() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Fork heads must present successful repository CI" in workflow
    assert "actions/workflows/ci.yml/runs?event=pull_request&head_sha=$head_sha" in workflow
    assert "any(.pull_requests[]?; .number == (env.PR_NUMBER | tonumber))" in workflow
    assert (
        "any(.pull_requests[]?; .number == (env.PR_NUMBER | tonumber) and .base.sha == env.BASE_SHA)"
        in workflow
    )
    assert 'if [[ "$ci_run_conclusion" != "success" ]]' in workflow
    assert 'if [[ "$is_cross_repo" != "true" ]]' not in workflow


def test_review_gate_does_not_merge_pull_requests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'gh pr merge "$pr_ref" --auto' not in workflow
    assert "name: Codex Review Gate" in workflow


def test_legacy_auto_merge_adapter_is_retired() -> None:
    retired = (ROOT / ".github" / "workflows" / "auto-merge.yml").read_text(encoding="utf-8")
    assert "name: Auto Merge (retired)" in retired
    assert "permissions: {}" in retired

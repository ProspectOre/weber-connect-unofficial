"""Native contracts for the generated canonical review gate adapter."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "review-gate.yml"
EVALUATOR = ROOT / ".github" / "review-gate" / "evaluate.sh"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _evaluator() -> str:
    return EVALUATOR.read_text(encoding="utf-8")


def test_active_workflow_is_canonical_adapter() -> None:
    w = _workflow()
    assert "Generated adapter" in w
    assert "github.workflow_sha" in w
    assert "classify_dependencies.py" in w
    assert 'bash "$CANONICAL_REVIEW_GATE"' in w


def test_review_verdict_rechecks_on_trusted_events() -> None:
    w = _workflow()
    assert "pull_request_target:" in w and "issue_comment:" in w
    assert "workflow_run:" in w and "workflows: [CI]" in w
    assert "Codex Review Signal" not in w


def test_review_event_reconciliation_uses_trusted_polling() -> None:
    assert not (ROOT / ".github/workflows/review-canonical-signal.yml").exists()
    assert "schedule:" in _workflow()
    assert "SIGNAL_TITLE" not in _workflow()


def test_manual_dispatch_rejects_malformed_pull_request_input() -> None:
    workflow = _workflow()
    discovery = workflow[workflow.index("- id: prs") : workflow.index("  evaluate:")]
    assert 'elif [[ "$EVENT_NAME" == "workflow_dispatch" ]]' in discovery
    assert "Manual review-gate dispatch requires a positive pull request number." in discovery
    assert "exit 1" in discovery


def test_workflow_has_exact_head_ci_proof() -> None:
    w = _workflow()
    assert "candidate-proof" in w
    assert "actions/workflows/ci.yml/runs?event=pull_request&head_sha=$head_sha" in w
    assert 'select(.name == "ci" and .app.slug == "github-actions")' in w
    assert '"$ci_check_head" != "$head_sha" || "$ci_conclusion" != "success"' in w


def test_ci_proof_binds_the_pr_before_recency() -> None:
    w = _workflow()
    assert "any(.pull_requests[]?; .number == (env.PR_NUMBER | tonumber))" in w
    assert "sort -t$'\\t' -k1,1nr" in w


def test_forks_cannot_skip_candidate_ci() -> None:
    w = _workflow()
    proof = w[w.index("- id: candidate-proof") : w.index("- name: Evaluate canonical gate")]
    assert 'if [[ "$head_repo" != "$REPO" ]]' not in proof
    assert "actions/workflows/ci.yml/runs?event=pull_request" in proof
    assert '"$ci_check_head" != "$head_sha" || "$ci_conclusion" != "success"' in proof


def test_ci_proof_exports_head_and_base_to_evaluator() -> None:
    w = _workflow()
    assert "EVENT_HEAD_SHA:" in w and "EXPECTED_BASE_SHA:" in w


def test_workflow_does_not_auto_merge() -> None:
    w = _workflow()
    assert "gh pr merge" not in w and "auto-merge-now" not in w


def test_workflow_uses_codex_identity() -> None:
    w = _workflow()
    assert "REVIEW_BOT: chatgpt-codex-connector[bot]" in w
    assert "REVIEW_BOT_LOGIN: chatgpt-codex-connector" in w


def test_evaluator_uses_codex_identity() -> None:
    e = _evaluator()
    assert "chatgpt-codex-connector[bot]" in e and "199175422" in e


def test_dependency_changes_require_regular_review() -> None:
    e = _evaluator()
    assert "DEPENDENCY_CLASSIFIER" not in e and "Dependencies exempt for" not in e
    result = subprocess.run(
        [sys.executable, str(EVALUATOR.with_name("classify_dependencies.py"))],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 3


def test_regular_review_rechecks_final_snapshot() -> None:
    e = _evaluator()
    assert "final_head_sha" in e and "final_base_sha" in e


def test_evaluator_disarms_preexisting_auto_merge() -> None:
    e = _evaluator()
    assert "disable_auto_merge" in e and 'auto_merge_enabled" == "true"' in e


def test_evaluator_keeps_review_gate_pending_until_clean() -> None:
    e = _evaluator()
    assert "Waiting for the regular review verdict" in e and "Clean regular review" in e


def test_native_clean_envelope_requires_known_details_boilerplate() -> None:
    evaluator = _evaluator()
    assert "your team has set up codex to review pull requests in this repo" in evaluator
    assert "<details>(?:(?!</details>).)*</details>" not in evaluator
    assert "strict_stock_clean_envelope" in evaluator
    assert "strict_stock_clean_issue_comment_envelope" in evaluator
    assert "here are some automated review suggestions for this pull request" in evaluator


def test_native_review_only_accepts_exact_stock_heading_and_intro_lines() -> None:
    evaluator = _evaluator()
    # The permissive fallback used to accept semantic text appended to either
    # stock envelope line, even when there were no inline finding threads.
    assert "codex[[:space:]]+review[^\\r\\n]*\\r?\\n" not in evaluator
    assert "suggestions for this pull request\\.[^\\r\\n]*\\r?\\n" not in evaluator


def test_exact_head_changes_requested_reviews_are_always_adverse() -> None:
    evaluator = _evaluator()
    assert 'select((.commit.oid // "") == $head)' in evaluator
    assert (
        'select(if .state == "CHANGES_REQUESTED" then true else ($body | exact_head) end)'
        in evaluator
    )
    assert 'clean: (.state != "CHANGES_REQUESTED"' in evaluator


def test_known_nonsemantic_stock_salutations_remain_accepted() -> None:
    evaluator = _evaluator()
    assert evaluator.count("def normalize_known_stock_salutation:") == 2
    for salutation in (
        "Keep it up!",
        "Keep them coming!",
        "You.re on a roll",
        "Swish!",
        "Bravo",
    ):
        assert salutation in evaluator


def test_evaluator_has_no_review_request_api() -> None:
    e = _evaluator()
    assert "request_reviewers" not in e and "requested_reviewers" not in e


def test_legacy_auto_merge_entrypoint_is_retired() -> None:
    t = (ROOT / ".github/workflows/auto-merge.yml").read_text(encoding="utf-8")
    assert "retired" in t.lower() and "review-gate.yml" in t


def test_event_only_capture_continues_past_draft_gate() -> None:
    evaluator = _evaluator()
    match = re.search(r"(?ms)^gate_pending\(\) \{\n.*?^\}", evaluator)
    assert match is not None
    draft_gate = evaluator.index('if [[ "$is_draft" == "true" ]]')
    event_capture = evaluator.index('if [[ "$event_name" == issue_comment && -f "$event_path" ]]')
    assert draft_gate < event_capture
    script = "\n".join(
        [
            "set -e",
            "evidence_only_mode=0",
            "event_history_phase_done=false",
            "RECORD_EVENT_ONLY=true",
            match.group(0),
            "gate_pending",
            "printf 'event-history-reached\\n'",
        ]
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout == "event-history-reached\n"

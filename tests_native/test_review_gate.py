"""Native contracts for the generated canonical review gate adapter."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "review-gate.yml"
EVALUATOR = ROOT / ".github" / "review-gate" / "evaluate.sh"
def _workflow() -> str: return WORKFLOW.read_text(encoding="utf-8")
def _evaluator() -> str: return EVALUATOR.read_text(encoding="utf-8")
def test_active_workflow_is_canonical_adapter() -> None:
    w = _workflow()
    assert "Generated adapter" in w
    assert "github.workflow_sha" in w
    assert "classify_dependencies.py" in w
    assert 'bash "$CANONICAL_REVIEW_GATE"' in w
def test_review_verdict_rechecks_on_trusted_events() -> None:
    w = _workflow()
    assert "pull_request_target:" in w and "issue_comment:" in w
    assert "workflow_run:" in w and "workflows: [CI, Codex Review Event Signal]" in w
    assert "Codex Review Event Signal" in w


def test_review_event_signal_is_privilege_free() -> None:
    signal = (ROOT / ".github/workflows/review-event-signal.yml").read_text(encoding="utf-8")
    assert "pull_request_review:" in signal
    assert "pull_request_review_comment:" in signal
    assert "permissions: {}" in signal
    assert "actions/checkout" not in signal
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


def test_fork_review_only_path_precedes_ci_lookup() -> None:
    w = _workflow()
    fork = w.index('head_repo="$(jq -er')
    branch = w.index('if [[ "$head_repo" != "$REPO" ]]', fork)
    ci = w.index("actions/workflows/ci.yml/runs?event=pull_request")
    assert fork < branch < ci
    assert "preserving the review-only path" in w
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
def test_evaluator_has_dependency_exemption() -> None:
    e = _evaluator()
    assert "DEPENDENCY_CLASSIFIER" in e and "Dependencies exempt for" in e
def test_dependency_exemption_rechecks_final_snapshot() -> None:
    e = _evaluator()
    assert "final_head_sha" in e and "final_base_sha" in e
def test_evaluator_disarms_preexisting_auto_merge() -> None:
    e = _evaluator()
    assert "disable_auto_merge" in e and 'auto_merge_enabled" == "true"' in e
def test_evaluator_keeps_review_gate_pending_until_clean() -> None:
    e = _evaluator()
    assert "Waiting for the regular review verdict" in e and "Clean regular review" in e
def test_evaluator_has_no_review_request_api() -> None:
    e = _evaluator()
    assert "request_reviewers" not in e and "requested_reviewers" not in e
def test_legacy_auto_merge_entrypoint_is_retired() -> None:
    t = (ROOT / ".github/workflows/auto-merge.yml").read_text(encoding="utf-8")
    assert "retired" in t.lower() and "review-gate.yml" in t

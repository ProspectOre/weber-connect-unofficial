# ruff: noqa: RUF001 -- the connector boilerplate contains its exact information glyph.
"""Executable contracts for the privileged review-verdict classifier."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto-merge.yml"
BOT = "chatgpt-codex-connector[bot]"
HEAD = "8a209f6b4b012f774a2a585d8ebc36259625f30c"


def _verdict_filter() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    begin = workflow.index("# REVIEW_VERDICT_FILTER_BEGIN")
    begin = workflow.index("\n", begin) + 1
    end = workflow.index("# REVIEW_VERDICT_FILTER_END", begin)
    return textwrap.dedent(workflow[begin:end])


def _ci_run_identity_filter() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    begin = workflow.index("# CI_RUN_IDENTITY_FILTER_BEGIN")
    begin = workflow.index("\n", begin) + 1
    end = workflow.index("# CI_RUN_IDENTITY_FILTER_END", begin)
    return textwrap.dedent(workflow[begin:end])


def _select_ci_run(payload: dict[str, object], *, pr_number: int) -> list[str]:
    completed = subprocess.run(
        ["jq", "-r", _ci_run_identity_filter()],
        input=json.dumps(payload),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PR_NUMBER": str(pr_number)},
    )
    candidates = [line.split("\t") for line in completed.stdout.splitlines()]
    return max(candidates, key=lambda candidate: int(candidate[0]), default=[])


def _classify(
    tmp_path: Path,
    *,
    reviews: list[dict[str, object]] | None = None,
    comments: list[dict[str, object]] | None = None,
    short_head: str = "",
) -> str:
    assert shutil.which("jq"), "the auto-merge runtime requires jq"
    review_pages = tmp_path / "reviews.json"
    comment_pages = tmp_path / "comments.json"
    review_pages.write_text(json.dumps([reviews or []]), encoding="utf-8")
    comment_pages.write_text(json.dumps([comments or []]), encoding="utf-8")
    completed = subprocess.run(
        [
            "jq",
            "-sr",
            "--arg",
            "bot",
            BOT,
            "--arg",
            "head",
            HEAD,
            "--arg",
            "prefix",
            HEAD[:10],
            "--arg",
            "short_head",
            short_head,
            _verdict_filter(),
            str(review_pages),
            str(comment_pages),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _formal(body: str) -> dict[str, object]:
    return {
        "user": {"login": BOT},
        "state": "COMMENTED",
        "commit_id": HEAD,
        "body": body,
    }


def _comment(body: str) -> dict[str, object]:
    return {"user": {"login": BOT}, "body": body}


def _codex_clean_body(marker: str, compliment: str = "Nice work!") -> str:
    return f"""Codex Review: Didn't find any major issues. {compliment}

**Reviewed commit:** `{marker}`

<details> <summary>ℹ️ About Codex in GitHub</summary>
<br/>

[Your team has set up Codex to review pull requests in this repo](https://chatgpt.com/codex/cloud/settings/general). Reviews are triggered when you
- Open a pull request for review
- Mark a draft as ready
- Comment "@codex review".

If Codex has suggestions, it will comment; otherwise it will react with 👍.




Codex can also answer questions or update the PR. Try commenting "@codex address that feedback".

</details>"""


def test_only_explicit_positive_contracts_grant(tmp_path: Path) -> None:
    clean = "## Review result: No issues found.\n\n**Reviewed commit:** `" + HEAD + "`"
    assert _classify(tmp_path, reviews=[_formal(clean)]) == "1 0"

    adverse = "## Review result: findings\nFindings for `" + HEAD + "`\n1. credentials leak"
    assert _classify(tmp_path, reviews=[_formal(adverse)]) == "0 1"


def test_negated_and_qualified_clean_words_fail_closed(tmp_path: Path) -> None:
    for body in (
        "Not LGTM: bearer credentials are forwarded to another host.",
        "No issues with tests, but credentials leak during redirects.",
        "Looks good except authorization is missing.",
    ):
        assert _classify(tmp_path, reviews=[_formal(body)]) == "0 0"


def test_clean_heading_with_later_caveat_fails_closed(tmp_path: Path) -> None:
    qualified_claude = (
        "## Review result: No issues found.\n\n"
        "One concern: redirects retain the Authorization header.\n\n"
        f"**Reviewed commit:** `{HEAD}`"
    )
    assert _classify(tmp_path, reviews=[_formal(qualified_claude)]) == "0 0"

    qualified_codex = _codex_clean_body(HEAD[:10]).replace(
        "\n<details>", "\nOne concern: redirects retain the Authorization header.\n\n<details>"
    )
    assert _classify(tmp_path, comments=[_comment(qualified_codex)], short_head=HEAD) == "0 0"


def test_codex_short_clean_marker_requires_unique_full_head_binding(tmp_path: Path) -> None:
    body = _codex_clean_body(HEAD[:10])
    assert _classify(tmp_path, comments=[_comment(body)]) == "0 0"
    assert _classify(tmp_path, comments=[_comment(body)], short_head=HEAD) == "1 0"


def test_codex_full_clean_marker_remains_supported(tmp_path: Path) -> None:
    body = _codex_clean_body(HEAD, "Keep it up!")
    assert _classify(tmp_path, comments=[_comment(body)]) == "1 0"


def test_auto_merge_requires_a_real_exact_head_ci_success() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "workflows: [CI]" in workflow
    assert "actions: read" in workflow
    assert "checks: read" in workflow
    assert "actions/workflows/ci.yml/runs?event=pull_request&head_sha=$head_sha" in workflow
    assert "check-runs?check_name=ci&filter=all&per_page=100" in workflow
    assert 'select(.name == "ci" and .app.slug == "github-actions")' in workflow
    assert 'GH_TOKEN="$STATUS_TOKEN" gh api' in workflow
    assert 'CI_RUN_ID="$ci_run_id"' in workflow
    assert '"$ci_check_head" != "$head_sha" || "$ci_conclusion" != "success"' in workflow
    assert '"$ci_run_event" != "pull_request"' in workflow
    assert '"$ci_run_conclusion" != "success"' in workflow
    assert '"$ci_run_path" != ".github/workflows/ci.yml"' in workflow
    assert 'PR_NUMBER="$pr_number"' in workflow
    assert "any(.pull_requests[]?; .number == (env.PR_NUMBER | tonumber))" in workflow
    assert "sort -t$'\\t' -k1,1nr" in workflow


def test_workflow_run_rechecks_every_associated_pr() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    associated = {"workflow_run": {"pull_requests": [{"number": 50}, {"number": 51}]}}

    assert [row["number"] for row in associated["workflow_run"]["pull_requests"]] == [50, 51]
    assert (
        "pr_number: ${{ github.event_name == 'workflow_run' "
        "&& github.event.workflow_run.pull_requests.*.number || fromJSON('[0]') }}"
    ) in workflow
    assert "EVENT_WORKFLOW_PR_NUMBER: ${{ matrix.pr_number }}" in workflow
    assert (
        "group: ${{ github.workflow }}-${{ github.event_name == 'workflow_run' "
        "&& matrix.pr_number || github.event.pull_request.number || "
        "github.event.issue.number || inputs.pull_request || github.ref }}"
    ) in workflow
    assert workflow.count("concurrency:") == 1
    assert (
        "EVENT_WORKFLOW_PR_NUMBER: ${{ github.event.workflow_run.pull_requests[0].number }}"
        not in workflow
    )


def test_ci_run_selection_filters_pr_before_recency() -> None:
    runs = {
        "workflow_runs": [
            {
                "id": 200,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": "success",
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 50}],
            },
            {
                "id": 100,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": "success",
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 49}],
            },
        ]
    }

    assert _select_ci_run(runs, pr_number=49) == [
        "100",
        HEAD,
        "pull_request",
        "success",
        ".github/workflows/ci.yml",
    ]


def test_ci_run_selection_prefers_newest_current_pr_run() -> None:
    runs = {
        "workflow_runs": [
            {
                "id": 100,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": "failure",
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 49}],
            },
            {
                "id": 300,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": "success",
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 49}],
            },
        ]
    }

    assert _select_ci_run(runs, pr_number=49)[0] == "300"


def test_ci_run_selection_does_not_fall_back_while_newest_is_pending() -> None:
    runs = {
        "workflow_runs": [
            {
                "id": 100,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": "success",
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 49}],
            },
            {
                "id": 300,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": None,
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 49}],
            },
        ]
    }

    assert _select_ci_run(runs, pr_number=49)[3] == "pending"


def test_ci_run_selection_rejects_missing_current_pr_association() -> None:
    runs = {
        "workflow_runs": [
            {
                "id": 200,
                "head_sha": HEAD,
                "event": "pull_request",
                "conclusion": "success",
                "path": ".github/workflows/ci.yml",
                "pull_requests": [{"number": 50}],
            }
        ]
    }

    assert _select_ci_run(runs, pr_number=49) == []


def test_fork_review_stamp_bypasses_ci_but_cannot_reach_auto_merge() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    ci_gate_start = workflow.index("# SAME_REPOSITORY_CI_GATE_BEGIN")
    ci_gate_end = workflow.index("# SAME_REPOSITORY_CI_GATE_END")
    ci_lookup = workflow.index("actions/workflows/ci.yml/runs?event=pull_request")
    evaluate = workflow.index("\n          evaluate_evidence\n")
    review_stamp = workflow.index("if ! stamp_review_gate success")
    fork_stop = workflow.index('if [[ "$is_cross_repo" == "true" ]]; then', review_stamp)
    auto_merge_arm = workflow.index('gh pr merge "$pr_ref" --auto --squash')

    assert 'if [[ "$is_cross_repo" != "true" ]]; then' in workflow[ci_gate_start:ci_gate_end]
    assert ci_gate_start < ci_lookup < ci_gate_end < evaluate
    assert evaluate < review_stamp < fork_stop < auto_merge_arm

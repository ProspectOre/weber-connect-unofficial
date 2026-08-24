"""Executable contracts for the privileged review-verdict classifier."""

from __future__ import annotations

import json
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


def test_codex_short_clean_marker_requires_unique_full_head_binding(tmp_path: Path) -> None:
    body = (
        "Codex Review: Didn't find any major issues. Nice work!\n\n"
        f"**Reviewed commit:** `{HEAD[:10]}`"
    )
    assert _classify(tmp_path, comments=[_comment(body)]) == "0 0"
    assert _classify(tmp_path, comments=[_comment(body)], short_head=HEAD) == "1 0"


def test_codex_full_clean_marker_remains_supported(tmp_path: Path) -> None:
    body = (
        f"Codex Review: Didn't find any major issues. Keep it up!\n\n**Reviewed commit:** `{HEAD}`"
    )
    assert _classify(tmp_path, comments=[_comment(body)]) == "1 0"

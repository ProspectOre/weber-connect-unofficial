"""Exercise the PR-edit gate while the code workflow creates its aggregate check."""

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/last-code-run.sh"


@pytest.mark.parametrize(
    ("responses", "running", "expected_code", "expected_reads", "expected_message"),
    [
        (
            [
                "",
                "in_progress null https://example.invalid/check",
                "completed success https://example.invalid/check",
            ],
            "queued",
            0,
            3,
            "Last code run of 'ci': success",
        ),
        ([""], "in_progress", 1, 3, "did not appear or finish in time"),
        ([""], None, 1, 1, "No active code run"),
        (
            ["completed failure https://example.invalid/check"],
            None,
            1,
            1,
            "Last code run of 'ci': failure",
        ),
    ],
)
def test_recheck_waits_for_aggregate(
    tmp_path: Path,
    responses: list[str],
    running: str | None,
    expected_code: int,
    expected_reads: int,
    expected_message: str,
) -> None:
    """Wait for an absent/running check without turning failures into success."""
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    for index, response in enumerate(responses):
        checks = [
            {
                "id": 1000,
                "app": {"slug": "other-app"},
                "check_suite": {"id": 100},
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 1001,
                "app": {"slug": "github-actions"},
                "check_suite": {"id": 200},
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 1002,
                "app": {"slug": "github-actions"},
                "check_suite": {"id": 201},
                "status": "completed",
                "conclusion": "success",
            },
            *(
                {
                    "id": 1003,
                    "app": {"slug": "github-actions"},
                    "check_suite": {"id": 100},
                    "status": "completed",
                    "conclusion": conclusion,
                }
                for conclusion in ("cancelled", "skipped")
            ),
        ]
        if response:
            status, conclusion, url = response.split()
            checks.append(
                {
                    "id": 1,
                    "app": {"slug": "github-actions"},
                    "check_suite": {"id": 100},
                    "status": status,
                    "conclusion": None if conclusion == "null" else conclusion,
                    "html_url": url,
                }
            )
        (response_dir / str(index)).write_text(json.dumps({"check_runs": checks}))
    (response_dir / "own").write_text(json.dumps({"check_suite_id": 200, "workflow_id": 17}))
    runs = [
        {"check_suite_id": suite, "status": status}
        for suite, status in [
            (200, "queued"),
            (201, "in_progress"),
            (100, "completed"),
            (100, "action_required"),
        ]
    ]
    if running:
        runs.append({"check_suite_id": 100, "status": running})
    (response_dir / "runs").write_text(json.dumps({"workflow_runs": runs}))
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        '#!/bin/sh\nset -eu\nargs="$*"\n'
        'while [ "$1" != --jq ]; do shift; done\nshift\nfilter="$1"\n'
        'case "$args" in\n'
        "*/actions/runs/2*) file=own ;;\n"
        "*/actions/workflows/17/runs*) file=runs ;;\n"
        '*/check-runs*) n=0; if [ -f "$RECHECK_COUNTER" ]; then read -r n < "$RECHECK_COUNTER"; fi\n'
        '   printf "%s\\n" "$((n + 1))" > "$RECHECK_COUNTER"\n'
        '   if [ "$n" -gt "$RECHECK_LAST_RESPONSE" ]; then n="$RECHECK_LAST_RESPONSE"; fi\n'
        '   file="$n" ;;\n*) exit 2 ;;\nesac\n'
        'exec jq -r "$filter" "$RECHECK_RESPONSES_DIR/$file"\n'
    )
    fake_gh.chmod(0o755)
    fake_sleep = tmp_path / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o755)
    # Exercise exhaustion without spending 110 rounds on process startup.
    fake_seq = tmp_path / "seq"
    fake_seq.write_text("#!/bin/sh\nprintf '1\\n2\\n3\\n'\n")
    fake_seq.chmod(0o755)
    counter = tmp_path / "reads"
    result = subprocess.run(
        ["bash", str(SCRIPT), "ci"],
        env={
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_RUN_ID": "2",
            "HEAD_SHA": "a" * 40,
            "RECHECK_COUNTER": str(counter),
            "RECHECK_RESPONSES_DIR": str(response_dir),
            "RECHECK_LAST_RESPONSE": str(len(responses) - 1),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == expected_code, result.stderr
    assert expected_message in result.stdout
    assert int(counter.read_text()) == expected_reads

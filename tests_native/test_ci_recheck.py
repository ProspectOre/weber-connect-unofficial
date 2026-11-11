"""Exercise the PR-edit gate while the code workflow creates its aggregate check."""

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
            1,
            0,
            3,
            "Last code run of 'ci': success",
        ),
        ([""], 1, 1, 3, "did not appear or finish in time"),
        ([""], 0, 1, 1, "No active code run"),
        (
            ["completed failure https://example.invalid/check"],
            0,
            1,
            1,
            "Last code run of 'ci': failure",
        ),
    ],
)
def test_recheck_waits_for_aggregate(
    tmp_path: Path,
    responses: list[str],
    running: int,
    expected_code: int,
    expected_reads: int,
    expected_message: str,
) -> None:
    """Wait for an absent/running check without turning failures into success."""
    response_dir = tmp_path / "responses"
    response_dir.mkdir()
    for index, response in enumerate(responses):
        (response_dir / str(index)).write_text(response + "\n")
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '*/actions/runs/*) printf "200 17\\n" ;;\n'
        '*/actions/workflows/*) printf "%s\\n" "$RECHECK_RUNNING" ;;\n'
        '*) n=0; if [ -f "$RECHECK_COUNTER" ]; then read -r n < "$RECHECK_COUNTER"; fi\n'
        '   printf "%s\\n" "$((n + 1))" > "$RECHECK_COUNTER"\n'
        '   if [ "$n" -gt "$RECHECK_LAST_RESPONSE" ]; then n="$RECHECK_LAST_RESPONSE"; fi\n'
        '   cat "$RECHECK_RESPONSES_DIR/$n" ;;\nesac\n'
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
            "RECHECK_RUNNING": str(running),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == expected_code, result.stderr
    assert expected_message in result.stdout
    assert int(counter.read_text()) == expected_reads

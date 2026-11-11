"""Exercise the PR-edit gate while the code workflow creates its aggregate check."""

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/last-code-run.sh"


@pytest.mark.parametrize(
    ("responses", "expected_code", "expected_reads", "expected_message"),
    [
        (
            [
                "",
                "in_progress null https://example.invalid/check",
                "completed success https://example.invalid/check",
            ],
            0,
            3,
            "Last code run of 'ci': success",
        ),
        ([""], 1, 110, "did not appear or finish in time"),
        (
            ["completed failure https://example.invalid/check"],
            1,
            1,
            "Last code run of 'ci': failure",
        ),
    ],
)
def test_recheck_waits_for_aggregate(
    tmp_path: Path,
    responses: list[str],
    expected_code: int,
    expected_reads: int,
    expected_message: str,
) -> None:
    """Wait for an absent/running check without turning failures into success."""
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "if '/actions/runs/' in sys.argv[2]:\n"
        "    print('200')\n"
        "else:\n"
        "    counter = pathlib.Path(os.environ['RECHECK_COUNTER'])\n"
        "    n = int(counter.read_text()) if counter.exists() else 0\n"
        "    responses = json.loads(os.environ['RECHECK_RESPONSES'])\n"
        "    print(responses[min(n, len(responses) - 1)])\n"
        "    counter.write_text(str(n + 1))\n"
    )
    fake_gh.chmod(0o755)
    fake_sleep = tmp_path / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o755)
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
            "RECHECK_RESPONSES": json.dumps(responses),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == expected_code, result.stderr
    assert expected_message in result.stdout
    assert int(counter.read_text()) == expected_reads

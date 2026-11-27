"""Offline checks for compatibility evidence and privacy-safe support triage."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from scripts.support import (
    RELEASES_URL,
    SupportError,
    _flatten_pages,
    render_markdown,
    render_triage,
    validate_registry,
)

ROOT = Path(__file__).resolve().parents[1]


def test_registry_and_generated_matrix_are_current() -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    validate_registry(registry, ROOT)
    assert render_markdown(registry, ROOT) == (ROOT / "docs/product-compatibility.md").read_text()
    assert len(registry["product_families"]) == 14


@pytest.mark.parametrize("publication", ["published_beta", "unpublished"])
def test_registry_distinguishes_publication_from_hardware_qualification(publication: str) -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    candidate = registry["current_candidate"]
    version = candidate["version"]
    candidate["publication_status"] = publication
    if publication == "unpublished":
        registry["observations"] = [
            row for row in registry["observations"] if row["integration_release"] != version
        ]
    markdown = render_markdown(registry, ROOT)
    expected_build = (
        f"Published beta [{version}]({RELEASES_URL}/tag/v{version})"
        if publication == "published_beta"
        else f"Unpublished candidate **{version}**"
    )
    assert f"{expected_build}. Full hardware qualification is pending." in markdown
    assert f"{RELEASES_URL}/latest" in markdown


def test_registry_rejects_an_invalid_candidate_publication_status() -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    registry["current_candidate"]["publication_status"] = "stable"
    with pytest.raises(SupportError, match="Candidate publication status"):
        validate_registry(registry, ROOT)


def test_rendered_evidence_links_resolve_from_matrix_directory() -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    markdown = render_markdown(registry, ROOT)
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", markdown)
    assert links
    version = registry["current_candidate"]["version"]
    release_links = {
        "latest stable release": f"{RELEASES_URL}/latest",
        version: f"{RELEASES_URL}/tag/v{version}",
    }
    repository_links = external_links = 0
    for label, destination in links:
        url = urlsplit(destination)
        if url.scheme:
            assert url.scheme == "https"
            assert destination == release_links.get(label, label)
            external_links += 1
            continue
        resolved = (ROOT / "docs" / unquote(url.path)).resolve()
        assert resolved.is_file(), destination
        assert resolved.is_relative_to(ROOT)
        if (ROOT / label).is_file():
            assert resolved == (ROOT / label).resolve()
        repository_links += 1
    assert repository_links > 0
    assert external_links > 0


def test_render_cli_works_when_invoked_by_path() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/support.py"), "render", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_scoped_beta_observations_do_not_qualify_the_physical_receipt() -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    validate_registry(registry, ROOT)
    physical = json.loads(
        (ROOT / registry["current_candidate"]["receipts"]["physical"]).read_text()
    )
    assert physical["status"] == "pending_hardware_validation"
    assert physical["verified"] == {}


@pytest.mark.parametrize(
    ("publication", "verification", "comment"),
    [
        ("unpublished", "community_verified", "#issuecomment-7212078167"),
        ("published_beta", "community_verified", ""),
        ("published_beta", "maintainer_verified", "#issuecomment-7212078167"),
    ],
)
def test_candidate_hardware_claim_requires_a_reporter_comment(
    publication: str, verification: str, comment: str
) -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    candidate = registry["current_candidate"]
    candidate["publication_status"] = publication
    observation = registry["observations"][0]
    observation["integration_release"] = candidate["version"]
    observation["verification_kind"] = verification
    observation["evidence"] = [
        {
            "kind": "url",
            "value": f"https://github.com/ProspectOre/weber-connect-unofficial/issues/59{comment}",
        }
    ]
    with pytest.raises(SupportError, match="published beta and a reporter comment"):
        validate_registry(registry, ROOT)


def test_published_beta_requires_a_release_candidate_version() -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    candidate = registry["current_candidate"]
    candidate.update(version="3.3.0", publication_status="published_beta")
    with pytest.raises(
        SupportError, match="published beta must use an exact release-candidate version"
    ):
        validate_registry(registry, ROOT)


def test_equal_but_stale_candidate_receipt_fingerprints_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = json.loads((ROOT / "docs/support/compatibility.json").read_text())
    from scripts import validate_release

    monkeypatch.setattr(validate_release, "runtime_fingerprint", lambda: "1" * 64)
    with pytest.raises(SupportError, match="current runtime fingerprint"):
        validate_registry(registry, ROOT)


def test_pr71_merged_comment_classifies_without_exporting_private_text() -> None:
    secret = "private-address@example.test"
    snapshot = {
        "repository": "ProspectOre/weber-connect-unofficial",
        "items": [
            {
                "number": 71,
                "title": "SmokeFire " + secret,
                "url": "https://github.com/ProspectOre/weber-connect-unofficial/pull/71",
                "author": secret,
                "state": "merged",
                "updated_at": "2026-10-01T00:00:00Z",
                "is_pull_request": True,
                "merged_at": "2026-09-01T00:00:00Z",
                "body": "manual cook",
                "comments": [
                    {
                        "body": "3.5-hour cook and cloud recovery",
                        "author": secret,
                        "created_at": "2026-09-02T00:00:00Z",
                        "updated_at": "2026-09-02T00:00:00Z",
                    }
                ],
            }
        ],
    }
    report = render_triage(snapshot)
    assert "PR #71" in report and "merged" in report
    assert "live readings" in report and "recovery" in report
    assert secret not in report


def test_triage_bounds_input_and_rejects_unknown_fields() -> None:
    with pytest.raises(SupportError, match="5000-item"):
        render_triage({"repository": "x/y", "items": [{}] * 5001})
    with pytest.raises(SupportError, match="unknown key"):
        render_triage({"repository": "x/y", "items": [{"unexpected": True}]})


def test_triage_does_not_export_supplied_url_or_arbitrary_state() -> None:
    item = {
        "number": 3,
        "title": "Report",
        "url": "https://github.com/acme/project/issues/3?token=private@example.test",
        "author": "reporter",
        "state": "open",
        "updated_at": "",
        "is_pull_request": False,
        "merged_at": None,
        "body": "",
        "comments": [],
    }
    report = render_triage({"repository": "acme/project", "items": [item]})
    assert "private@example.test" not in report
    assert "https://github.com/acme/project/issues/3" in report
    item["state"] = "open private@example.test"
    with pytest.raises(SupportError, match="invalid state"):
        render_triage({"repository": "acme/project", "items": [item]})


def test_paginated_github_data_must_be_slurp_lists() -> None:
    assert _flatten_pages('[[{"number": 1}], []]', "issues") == [{"number": 1}]
    with pytest.raises(SupportError):
        _flatten_pages('[{"number": 1}]', "issues")

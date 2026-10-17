#!/usr/bin/env python3
"""Validate compatibility evidence, render its matrix, and triage GitHub read-only."""

from __future__ import annotations

import argparse
import json
import re
import shutil

# Fixed read-only gh API argv; shell execution is never used.
import subprocess  # nosec B404
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Make the sibling scripts namespace importable when invoked as a file path,
# including the runtime fingerprint helper used for checked-out receipts.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REGISTRY_PATH = ROOT / "docs/support/compatibility.json"
MATRIX_PATH = ROOT / "docs/product-compatibility.md"
TRIAGE_GUIDE = "docs/support/triage.md"
TRIAL_GUIDE = "docs/support/hardware-trials.md"

FAMILY_IDS = {
    "connect_smart_grilling_hub",
    "smokefire",
    "searwood_smoque",
    "spirit_smart",
    "genesis_smart",
    "summit_smart",
    "performer_smart",
    "kettle_smart_ring",
    "lumin_smart",
    "newer_hubs_boosters",
    "portable_probes",
    "pulse_igrill",
    "other_non_connected_grills",
}
IMPLEMENTATION_STATUSES = {
    "implemented",
    "partially_implemented",
    "instrumented",
    "qualification_required",
    "not_supported",
}
CANDIDATE_STATUSES = {"pending_hardware_validation"}
OBSERVATION_KINDS = {"community_verified", "maintainer_verified"}
STAGES = {
    "discovery",
    "pairing",
    "cloud_association",
    "live_readings",
    "app_coexistence",
    "restart_recovery",
    "stable_entity_ids",
    "cook_session",
    "endurance",
    "probe_slot_mapping",
}
FEEDBACK_STATUSES = {
    "fixed_in_candidate",
    "instrumented_unresolved",
    "unresolved",
    "instrumented",
}
ROOT_KEYS = {
    "schema_version",
    "current_candidate",
    "product_families",
    "observations",
    "feedback",
    "product_sources",
}


class SupportError(ValueError):
    """Invalid evidence data or an incomplete GitHub read."""


def _expect_keys(
    value: dict[str, Any], allowed: set[str], required: set[str], context: str
) -> None:
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise SupportError(f"{context} has unknown key(s): {', '.join(sorted(unknown))}")
    if missing:
        raise SupportError(f"{context} is missing key(s): {', '.join(sorted(missing))}")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupportError(f"Could not read JSON file: {path}") from exc


def _repo_path(value: Any, root: Path, context: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SupportError(f"{context} must be a repository-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not (root / path).is_file():
        raise SupportError(f"{context} references a missing or unsafe repository path")
    return root / path


def _source_reference(source: Any, root: Path, context: str) -> tuple[str, str]:
    if not isinstance(source, dict):
        raise SupportError(f"{context} must be an object")
    _expect_keys(source, {"kind", "value"}, {"kind", "value"}, context)
    kind, value = source["kind"], source["value"]
    if kind == "repository_path":
        _repo_path(value, root, context)
        return kind, value
    if kind == "url" and isinstance(value, str):
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "github.com"
            and parsed.path.startswith("/ProspectOre/weber-connect-unofficial/")
        ):
            return kind, value
    raise SupportError(f"{context} must be an HTTPS source URL or repository path")


def validate_registry(registry: dict[str, Any], root: Path = ROOT) -> None:
    """Fail closed on schema drift, missing references, or unsupported evidence claims."""

    if not isinstance(registry, dict):
        raise SupportError("Registry root must be an object")
    _expect_keys(registry, ROOT_KEYS, ROOT_KEYS, "registry")
    if registry["schema_version"] != 1:
        raise SupportError("Unsupported compatibility registry schema_version")

    candidate = registry["current_candidate"]
    _expect_keys(
        candidate,
        {"version", "publication_status", "hardware_status", "receipts"},
        {"version", "publication_status", "hardware_status", "receipts"},
        "current_candidate",
    )
    version = candidate["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:rc\d+)?", version):
        raise SupportError("current_candidate.version must be an exact version")
    if candidate["publication_status"] != "unpublished":
        raise SupportError("This registry requires an explicitly unpublished candidate")
    if candidate["hardware_status"] != "pending_hardware_validation":
        raise SupportError("Current candidate hardware status must remain pending")
    receipts = candidate["receipts"]
    _expect_keys(
        receipts,
        {"readiness", "automated", "physical"},
        {"readiness", "automated", "physical"},
        "candidate receipts",
    )
    readiness_path = _repo_path(receipts["readiness"], root, "readiness receipt")
    automated_path = _repo_path(receipts["automated"], root, "automated receipt")
    physical_path = _repo_path(receipts["physical"], root, "physical receipt")
    automated = _load_json(automated_path)
    physical = _load_json(physical_path)
    readiness = readiness_path.read_text(encoding="utf-8")
    if automated.get("candidate") != version or physical.get("candidate") != version:
        raise SupportError("Candidate receipt version does not match current_candidate.version")
    if automated.get("runtime_sha256") != physical.get("runtime_sha256"):
        raise SupportError("Automated and physical receipts have different runtime fingerprints")
    # For the checked-out repository, bind both receipts to the shipped runtime
    # using the release validator's canonical algorithm. Temporary fixture roots
    # remain usable for schema-focused tests without pretending to be this checkout.
    if root.resolve() == ROOT.resolve():
        from scripts.validate_release import runtime_fingerprint

        if automated.get("runtime_sha256") != runtime_fingerprint():
            raise SupportError("Candidate receipts do not match the current runtime fingerprint")
    if version not in readiness:
        raise SupportError("Readiness document does not refer to the current candidate")
    if physical.get("status") != "pending_hardware_validation" or physical.get("verified"):
        raise SupportError("Current candidate physical receipt is not pending and empty")

    families = registry["product_families"]
    if not isinstance(families, list):
        raise SupportError("product_families must be a list")
    family_by_id: dict[str, dict[str, Any]] = {}
    family_keys = {
        "id",
        "name",
        "implementation_status",
        "implementation_summary",
        "implementation_evidence",
        "candidate_status",
        "candidate_receipt",
    }
    for family in families:
        if not isinstance(family, dict):
            raise SupportError("Every product family must be an object")
        _expect_keys(family, family_keys, family_keys, "product family")
        family_id = family["id"]
        if not isinstance(family_id, str) or family_id in family_by_id:
            raise SupportError("Product family IDs must be unique strings")
        if family["implementation_status"] not in IMPLEMENTATION_STATUSES:
            raise SupportError(f"Invalid implementation status for {family_id}")
        if family["candidate_status"] not in CANDIDATE_STATUSES:
            raise SupportError(f"Invalid candidate status for {family_id}")
        if family["candidate_receipt"] != receipts["physical"]:
            raise SupportError(f"Candidate receipt mismatch for {family_id}")
        if not all(
            isinstance(family[key], str) and family[key]
            for key in ("name", "implementation_summary")
        ):
            raise SupportError(
                f"Product family {family_id} needs a name and implementation summary"
            )
        evidence = family["implementation_evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise SupportError(f"Positive implementation status for {family_id} needs evidence")
        for reference in evidence:
            _repo_path(reference, root, f"implementation evidence for {family_id}")
        family_by_id[family_id] = family
    if set(family_by_id) != FAMILY_IDS:
        missing = sorted(FAMILY_IDS - set(family_by_id))
        extra = sorted(set(family_by_id) - FAMILY_IDS)
        raise SupportError(f"Product family inventory mismatch (missing={missing}, extra={extra})")

    observations = registry["observations"]
    if not isinstance(observations, list):
        raise SupportError("observations must be a list")
    observation_ids: set[str] = set()
    observation_keys = {
        "id",
        "product_family",
        "exact_model",
        "firmware",
        "integration_release",
        "verification_kind",
        "passed_stages",
        "evidence",
        "notes",
    }
    receipt_stage_keys = {
        "discovery": {"physical_confirmation_through_proxy"},
        "pairing": {"clean_install_private_companion", "physical_confirmation_through_proxy"},
        "cloud_association": {"exact_cloud_association"},
        "live_readings": {"simultaneous_official_app_and_home_assistant"},
        "app_coexistence": {"simultaneous_official_app_and_home_assistant"},
        "restart_recovery": {"home_assistant_restart_without_repair", "proxy_reboot_recovery"},
        "stable_entity_ids": {"four_stable_probe_entities"},
        "cook_session": {"cloud_active_cook_endurance_minutes"},
        "endurance": {"cloud_active_cook_endurance_minutes", "proxy_only_endurance_minutes"},
        "probe_slot_mapping": {"four_stable_probe_entities"},
    }
    for observation in observations:
        if not isinstance(observation, dict):
            raise SupportError("Every observation must be an object")
        _expect_keys(observation, observation_keys, observation_keys, "hardware observation")
        observation_id = observation["id"]
        if not isinstance(observation_id, str) or observation_id in observation_ids:
            raise SupportError("Observation IDs must be unique strings")
        observation_ids.add(observation_id)
        if observation["product_family"] not in family_by_id:
            raise SupportError(f"Unknown observation family in {observation_id}")
        if (
            not isinstance(observation["exact_model"], str)
            or not observation["exact_model"].strip()
        ):
            raise SupportError(f"Observation {observation_id} needs an exact model")
        if observation["firmware"] is not None and not isinstance(observation["firmware"], str):
            raise SupportError(f"Observation {observation_id} firmware must be text or null")
        release = observation["integration_release"]
        if not isinstance(release, str) or not release:
            raise SupportError(f"Observation {observation_id} needs an exact integration release")
        if release == version:
            raise SupportError(
                "Current candidate cannot inherit or claim historical hardware verification"
            )
        if observation["verification_kind"] not in OBSERVATION_KINDS:
            raise SupportError(f"Invalid verification kind in {observation_id}")
        stages = observation["passed_stages"]
        if (
            not isinstance(stages, list)
            or not stages
            or any(stage not in STAGES for stage in stages)
        ):
            raise SupportError(f"Observation {observation_id} needs valid passed stages")
        evidence = observation["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise SupportError(f"Positive observation {observation_id} needs provenance")
        for reference in evidence:
            kind, value = _source_reference(reference, root, f"evidence for {observation_id}")
            if kind == "repository_path" and value.endswith(".json"):
                receipt = _load_json(root / value)
                if receipt.get("candidate") != release:
                    raise SupportError(
                        f"Observation release does not match receipt for {observation_id}"
                    )
                verified = receipt.get("verified", {})
                for stage in stages:
                    if not (receipt_stage_keys[stage] & set(verified)) or not any(
                        verified.get(key) for key in receipt_stage_keys[stage]
                    ):
                        raise SupportError(f"Receipt does not verify {stage} for {observation_id}")

    feedback = registry["feedback"]
    if not isinstance(feedback, list):
        raise SupportError("feedback must be a list")
    feedback_ids: set[str] = set()
    feedback_keys = {"id", "summary", "status", "product_families", "sources", "disposition"}
    for item in feedback:
        if not isinstance(item, dict):
            raise SupportError("Every feedback item must be an object")
        _expect_keys(item, feedback_keys, feedback_keys, "feedback item")
        feedback_id = item["id"]
        if not isinstance(feedback_id, str) or feedback_id in feedback_ids:
            raise SupportError("Feedback IDs must be unique strings")
        feedback_ids.add(feedback_id)
        if item["status"] not in FEEDBACK_STATUSES:
            raise SupportError(f"Invalid feedback status for {feedback_id}")
        if not all(isinstance(item[key], str) and item[key] for key in ("summary", "disposition")):
            raise SupportError(f"Feedback {feedback_id} needs summary and disposition")
        if not isinstance(item["product_families"], list) or any(
            family_id not in family_by_id for family_id in item["product_families"]
        ):
            raise SupportError(f"Feedback {feedback_id} references an unknown family")
        sources = item["sources"]
        if not isinstance(sources, list) or not sources:
            raise SupportError(f"Feedback {feedback_id} needs at least one source")
        for source in sources:
            if source.startswith("https://") if isinstance(source, str) else False:
                _source_reference({"kind": "url", "value": source}, root, feedback_id)
            else:
                _repo_path(source, root, f"feedback source for {feedback_id}")

    sources = registry["product_sources"]
    if not isinstance(sources, list) or not sources:
        raise SupportError("product_sources must be a non-empty list")
    for source in sources:
        parsed = urlsplit(source) if isinstance(source, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.netloc:
            raise SupportError("product_sources must be HTTPS URLs")


def _md_link(value: str, root: Path = ROOT) -> str:
    if value.startswith("https://"):
        return f"[{value}]({value})"
    return f"[{value}]({value})"


def render_markdown(registry: dict[str, Any], root: Path = ROOT) -> str:
    """Render the compatibility registry as compact, deterministic Markdown."""

    validate_registry(registry, root)
    candidate = registry["current_candidate"]
    receipts = candidate["receipts"]
    lines = [
        "# Product compatibility and hardware observations",
        "",
        "This generated inventory separates implementation status from version-bound hardware evidence. Product applicability is not a compatibility claim. See the [hardware trial procedure](support/hardware-trials.md) and [read-only support triage](support/triage.md).",
        "",
        f"Current candidate: **{candidate['version']}** ({candidate['publication_status']}; hardware: {candidate['hardware_status']}).",
        "",
        "| Candidate receipt | Record |",
        "| --- | --- |",
        f"| Readiness | {_md_link(receipts['readiness'])} |",
        f"| Automated | {_md_link(receipts['automated'])} |",
        f"| Physical | {_md_link(receipts['physical'])} |",
        "",
        "## Product lines",
        "",
        "| Product line | Implementation status | Current candidate hardware status |",
        "| --- | --- | --- |",
    ]
    for family in registry["product_families"]:
        summary = family["implementation_summary"].replace("|", "\\|")
        lines.append(
            f"| {family['name']} | **{family['implementation_status']}** — {summary} | {family['candidate_status']} ({_md_link(family['candidate_receipt'])}) |"
        )
    lines.extend(
        [
            "",
            "## Version-bound hardware observations",
            "",
            "These observations apply only to the exact model, firmware and integration release shown. They do not carry forward to RC4.",
            "",
            "| Model | Firmware | Integration release | Verification | Passed stages | Evidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    family_names = {row["id"]: row["name"] for row in registry["product_families"]}
    for observation in registry["observations"]:
        evidence = ", ".join(_md_link(row["value"]) for row in observation["evidence"])
        firmware = (
            observation["firmware"] if observation["firmware"] is not None else "Not reported"
        )
        stages = ", ".join(stage.replace("_", " ") for stage in observation["passed_stages"])
        model = observation["exact_model"].replace("|", "\\|")
        lines.append(
            f"| {family_names[observation['product_family']]}: {model} | {firmware} | {observation['integration_release']} | {observation['verification_kind']} | {stages} | {evidence} |"
        )
    lines.extend(
        [
            "",
            "## User feedback and disposition",
            "",
            "| Feedback | Status | Disposition | Sources |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in registry["feedback"]:
        summary = item["summary"].replace("|", "\\|")
        disposition = item["disposition"].replace("|", "\\|")
        sources = ", ".join(_md_link(source) for source in item["sources"])
        lines.append(f"| {summary} | {item['status']} | {disposition} | {sources} |")
    sources = ", ".join(_md_link(source) for source in registry["product_sources"])
    lines.extend(["", f"Product applicability sources: {sources}", ""])
    rendered = "\n".join(lines)
    if len(lines) >= 150:
        raise SupportError("Generated compatibility matrix must remain under 150 lines")
    return rendered


def render(root: Path = ROOT, check: bool = False) -> None:
    registry = _load_json(root / "docs/support/compatibility.json")
    content = render_markdown(registry, root)
    target = root / "docs/product-compatibility.md"
    if check:
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            raise SupportError("Generated product compatibility document is out of date")
        return
    target.write_text(content, encoding="utf-8")


def _flatten_pages(raw: str, endpoint: str) -> list[Any]:
    try:
        pages = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SupportError(f"GitHub returned invalid JSON for {endpoint}") from exc
    if not isinstance(pages, list):
        raise SupportError(f"GitHub response for {endpoint} was not paginated JSON")
    flattened: list[Any] = []
    for page in pages:
        if not isinstance(page, list):
            raise SupportError(f"GitHub response page for {endpoint} was not a list")
        flattened.extend(page)
    return flattened


def _gh_api(endpoint: str) -> Any:
    executable = shutil.which("gh")
    if executable is None:
        raise SupportError("GitHub CLI (gh) is not installed")
    # Args are constructed from validated OWNER/REPO and positive integer IDs;
    # shell execution is disabled and this command only reads GitHub API data.
    result = subprocess.run(  # nosec B603
        [executable, "api", "--paginate", "--slurp", endpoint],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise SupportError(f"GitHub read failed for {endpoint}")
    return _flatten_pages(result.stdout, endpoint)


def fetch_github_snapshot(repository: str) -> dict[str, Any]:
    """Read every issue/PR and all issue-level comments with authenticated gh CLI."""

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SupportError("Repository must use OWNER/REPO form")
    items = _gh_api(f"repos/{repository}/issues?state=all&per_page=100")
    normalized: list[dict[str, Any]] = []
    for item in items:
        number = item.get("number")
        if type(number) is not int or number < 1:
            raise SupportError("GitHub returned an item without a valid issue number")
        is_pr = isinstance(item.get("pull_request"), dict)
        merged_at = None
        state = item.get("state", "unknown")
        if is_pr:
            executable = shutil.which("gh")
            if executable is None:
                raise SupportError("GitHub CLI (gh) is not installed")
            pr_response = subprocess.run(  # nosec B603
                [executable, "api", f"repos/{repository}/pulls/{number}"],
                capture_output=True,
                check=False,
                text=True,
            )
            if pr_response.returncode:
                raise SupportError(f"GitHub pull request read failed for #{number}")
            try:
                pr = json.loads(pr_response.stdout)
            except json.JSONDecodeError as exc:
                raise SupportError(
                    f"GitHub returned invalid pull request JSON for #{number}"
                ) from exc
            merged_at = pr.get("merged_at")
            if merged_at:
                state = "merged"
        comments: list[dict[str, Any]] = []
        if item.get("comments", 0):
            comments = _gh_api(f"repos/{repository}/issues/{number}/comments?per_page=100")
        normalized.append(
            {
                "number": number,
                "title": item.get("title") or "Untitled report",
                "url": item.get("html_url") or "",
                "author": (item.get("user") or {}).get("login") or "unknown",
                "state": state,
                "updated_at": item.get("updated_at") or "",
                "is_pull_request": is_pr,
                "merged_at": merged_at,
                "body": item.get("body") or "",
                "comments": [
                    {
                        "body": row.get("body") or "",
                        "author": (row.get("user") or {}).get("login") or "unknown",
                        "created_at": row.get("created_at") or "",
                        "updated_at": row.get("updated_at") or "",
                    }
                    for row in comments
                ],
            }
        )
    normalized.sort(key=lambda row: row["number"])
    return {"repository": repository, "items": normalized}


def _timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _classify(item: dict[str, Any]) -> list[str]:
    text = " ".join(
        [item["title"], item["body"], *(row["body"] for row in item["comments"])]
    ).casefold()
    signals = {
        "discovery": ("discover", "advertisement", "found the device"),
        "pairing": ("pair", "approval", "pairing prompt", "physical confirmation"),
        "cloud association": ("cloud", "appliance id", "association"),
        "live readings": ("reading", "temperature", "cook", "fuel", "telemetry"),
        "recovery": ("recovery", "reconnect", "restart", "reload", "disconnect"),
        "entity behavior": ("entity", "entity id", "nickname", "sensor"),
    }
    stages = [label for label, words in signals.items() if any(word in text for word in words)]
    return stages or ["Unclassified; manual triage required"]


def _guidance(item: dict[str, Any], stages: list[str]) -> tuple[str, str]:
    number = item["number"]
    if number == 59:
        return (
            "Exact thermometer/controller model and firmware, whether a physical approval prompt appeared, last successful stage, and a reviewed privacy-safe support report.",
            "Maintainer: keep unresolved until the reporter supplies the exact device evidence and approval result; do not infer a pairing fix.",
        )
    if number == 65:
        return (
            "Exact Genesis controller and firmware, sanitized manufacturer/GATT structure, physical approval outcome, and cloud/live-reading result.",
            "Maintainer: compare the reported frame shape with a sanitized fixture; preserve the framing failure until a physical trial confirms the outcome.",
        )
    if item["is_pull_request"] and number == 71:
        return (
            "Exact candidate release, full-cook readings, fuel unknowns, cloud-gap timings, app coexistence, restart/recovery, stable IDs and a separate current-candidate trial.",
            "Maintainer: keep the RC3 community observation version-bound and request a new RC4 hardware run before changing compatibility status.",
        )
    if stages == ["Unclassified; manual triage required"]:
        return (
            "Exact product/generation, firmware, integration and Home Assistant versions, failing stage, and a reviewed privacy-safe support report.",
            "Maintainer: manually classify this report and request only the missing reproducible evidence; do not treat silence as resolution.",
        )
    return (
        "Exact product/generation, firmware, integration and Home Assistant versions, outcome for each relevant stage, and a reviewed privacy-safe support report.",
        "Maintainer: review the affected stage and preserve the report as unresolved until the requested evidence supports a disposition.",
    )


def render_triage(snapshot: dict[str, Any]) -> str:
    _expect_keys(snapshot, {"repository", "items"}, {"repository", "items"}, "triage snapshot")
    repository = snapshot["repository"]
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise SupportError("Snapshot repository must use OWNER/REPO form")
    items = snapshot["items"]
    if not isinstance(items, list):
        raise SupportError("Snapshot items must be a list")
    lines = [
        f"# Read-only support triage: {repository}",
        "",
        "Generated from issue and pull-request issue-level comment metadata. Comment text is used only to classify stages and is never copied here. This report does not change GitHub records.",
        "",
    ]
    seen_numbers: set[int] = set()
    if len(items) > 5000:
        raise SupportError("Triage snapshot exceeds the 5000-item limit")
    for item in sorted(items, key=lambda row: row.get("number", 0)):
        required = {
            "number",
            "title",
            "url",
            "author",
            "state",
            "updated_at",
            "is_pull_request",
            "merged_at",
            "body",
            "comments",
        }
        _expect_keys(item, required, required, "triage item")
        number = item["number"]
        if type(number) is not int or number < 1 or number in seen_numbers:
            raise SupportError("Triage item numbers must be unique positive integers")
        seen_numbers.add(number)
        if not all(
            isinstance(item[key], str)
            for key in ("title", "url", "author", "state", "updated_at", "body")
        ):
            raise SupportError(f"Triage item #{number} has invalid text fields")
        if type(item["is_pull_request"]) is not bool or not isinstance(item["comments"], list):
            raise SupportError(f"Triage item #{number} has invalid type or comments")
        if item["state"] not in {"open", "closed", "merged"}:
            raise SupportError(f"Triage item #{number} has invalid state")
        comments = item["comments"]
        if len(comments) > 2000 or len(item["body"]) > 100_000:
            raise SupportError(f"Triage item #{number} exceeds bounded input limits")
        for comment in comments:
            _expect_keys(
                comment,
                {"body", "author", "created_at", "updated_at"},
                {"body", "author", "created_at", "updated_at"},
                f"comment on #{number}",
            )
            if not all(isinstance(comment[key], str) for key in comment):
                raise SupportError(f"Comment on #{number} has invalid fields")
            if len(comment["body"]) > 100_000:
                raise SupportError(f"Comment on #{number} exceeds bounded input limits")
        stages = _classify(item)
        evidence_needed, next_step = _guidance(item, stages)
        comments_dates = [
            date
            for comment in comments
            for date in (_timestamp(comment["updated_at"]), _timestamp(comment["created_at"]))
            if date is not None
        ]
        item_date = _timestamp(item["updated_at"])
        updated = max(
            [date for date in [item_date, *comments_dates] if date is not None], default=None
        )
        updated_text = updated.isoformat().replace("+00:00", "Z") if updated else "Unknown"
        record_type = "PR" if item["is_pull_request"] else "Issue"
        state = item["state"]
        if item["is_pull_request"] and item["merged_at"]:
            state = "merged"
        stage_text = ", ".join(stages)
        # Titles, authors, and bodies may contain customer identifiers. Classify them,
        # but emit only an opaque record heading and aggregate stage labels.
        lines.extend(
            [
                f"## {record_type} #{number}",
                "",
                "- **Owner:** Maintainer",
                f"- **State:** {state}; updated {updated_text}",
                f"- **Stages:** {stage_text}",
                f"- **Evidence needed:** {evidence_needed}",
                f"- **Next step:** {next_step}",
                f"- **Source:** https://github.com/{repository}/{'pull' if item['is_pull_request'] else 'issues'}/{number}",
                "",
            ]
        )
    if len(lines) == 4:
        lines.append("No issues or pull requests were returned.")
    return "\n".join(lines)


def _write_triage(snapshot: dict[str, Any], output: Path) -> None:
    output.write_text(render_triage(snapshot), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="validate the compatibility registry")
    validate_parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    render_parser = commands.add_parser("render", help="render the generated product matrix")
    render_parser.add_argument(
        "--check", action="store_true", help="fail if generated Markdown is stale"
    )
    render_parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    triage_parser = commands.add_parser("triage", help="generate a local, read-only support report")
    source_group = triage_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--github", help="authenticated GitHub OWNER/REPO to read")
    source_group.add_argument("--input", type=Path, help="offline normalized GitHub snapshot JSON")
    triage_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            registry = _load_json(args.registry)
            validate_registry(registry, args.registry.resolve().parents[2])
            print("Compatibility registry is valid.")
        elif args.command == "render":
            root = args.registry.resolve().parents[2]
            registry = _load_json(args.registry)
            content = render_markdown(registry, root)
            target = root / "docs/product-compatibility.md"
            if args.check:
                if not target.is_file() or target.read_text(encoding="utf-8") != content:
                    raise SupportError("Generated product compatibility document is out of date")
                print("Generated product compatibility document is current.")
            else:
                target.write_text(content, encoding="utf-8")
                print(f"Wrote {target.relative_to(root)}")
        else:
            if args.github:
                snapshot = fetch_github_snapshot(args.github)
            else:
                snapshot = _load_json(args.input)
            _write_triage(snapshot, args.output)
            print(f"Wrote read-only triage report to {args.output}")
    except SupportError as exc:
        print(f"support: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

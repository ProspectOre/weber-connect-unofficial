#!/usr/bin/env python3
"""Validate the native Home Assistant integration release contract."""

from __future__ import annotations

import ast
import json
import py_compile
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "weber_connect"
VERSION = "3.1.2"
# A presentation-only release may reuse evidence for an unchanged runtime. Keep
# each exception keyed to the exact release so changing VERSION automatically
# requires matching fresh evidence unless a new exception is deliberately added.
PRESENTATION_ONLY_RUNTIME_EVIDENCE = {"3.1.2": "3.1.1"}
RUNTIME_EVIDENCE_VERSION = PRESENTATION_ONLY_RUNTIME_EVIDENCE.get(VERSION, VERSION)
DOMAIN = "weber_connect"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        fail(f"{path.relative_to(ROOT)} must contain a JSON object")
    return payload


def check_required_files() -> None:
    required = (
        "README.md",
        "ARCHITECTURE.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "PRODUCTION_READINESS.md",
        "docs/validation/3.0.0-rc-automated.json",
        "docs/validation/3.0.0-rc-physical.json",
        "docs/validation/3.1.0-rc-automated.json",
        "docs/validation/3.1.0-rc-physical.json",
        "docs/validation/3.1.1-rc-automated.json",
        "docs/validation/3.1.1-rc-physical.json",
        "SECURITY.md",
        "hacs.json",
        "requirements-runtime.txt",
        "custom_components/weber_connect/__init__.py",
        "custom_components/weber_connect/manifest.json",
        "custom_components/weber_connect/strings.json",
        "custom_components/weber_connect/config_flow.py",
        "custom_components/weber_connect/bluetooth.py",
        "custom_components/weber_connect/coordinator.py",
        "custom_components/weber_connect/sensor.py",
        "custom_components/weber_connect/diagnostics.py",
        "custom_components/weber_connect/options.py",
        "custom_components/weber_connect/repairs.py",
        "custom_components/weber_connect/translations/en.json",
        "tests_native/test_config_flow.py",
        "tests_native/test_bluetooth.py",
    )
    for relative in required:
        if not (ROOT / relative).is_file():
            fail(f"missing required file: {relative}")
    for removed in ("repository.yaml", "weber_connect_ble"):
        if (ROOT / removed).exists():
            fail(f"legacy add-on artifact must not ship: {removed}")


def check_manifest() -> None:
    manifest = load_json(INTEGRATION / "manifest.json")
    hacs = load_json(ROOT / "hacs.json")
    expected = {
        "domain": DOMAIN,
        "version": VERSION,
        "config_flow": True,
        "integration_type": "hub",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            fail(f"manifest.json {key} must be {value!r}")
    if "Unofficial" not in str(manifest.get("name")):
        fail("manifest name must visibly identify the integration as unofficial")
    if manifest.get("dependencies") != ["bluetooth_adapters"]:
        fail("manifest must depend on bluetooth_adapters for proxy readiness")
    bluetooth = manifest.get("bluetooth")
    if not isinstance(bluetooth, list):
        fail("manifest must declare Bluetooth discovery matchers")
    manufacturer_ids = {row.get("manufacturer_id") for row in bluetooth if isinstance(row, dict)}
    if not {0x0DF2, 0x07C5} <= manufacturer_ids:
        fail("manifest is missing Weber and legacy manufacturer matchers")
    if hacs.get("homeassistant") != "2026.7.0":
        fail("hacs.json minimum Home Assistant version changed unexpectedly")
    runtime = {
        line.strip()
        for line in (ROOT / "requirements-runtime.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if set(manifest.get("requirements", [])) != runtime:
        fail("manifest requirements must match requirements-runtime.txt")


def check_translations() -> None:
    strings = load_json(INTEGRATION / "strings.json")
    translations = load_json(INTEGRATION / "translations" / "en.json")
    if strings != translations:
        fail("strings.json and translations/en.json must stay synchronized")
    for section in ("config", "options", "entity"):
        if not isinstance(translations.get(section), dict):
            fail(f"English translations are missing {section}")
    issues = translations.get("issues")
    if not isinstance(issues, dict):
        fail("English translations are missing issues")
    for issue_key, issue in issues.items():
        if not isinstance(issue, dict):
            fail(f"issue translation {issue_key} must be an object")
        unsupported = set(issue) - {"title", "fix_flow"}
        if unsupported:
            fail(
                f"issue translation {issue_key} contains unsupported top-level keys: "
                f"{', '.join(sorted(unsupported))}"
            )
        if not isinstance(issue.get("title"), str) or not isinstance(issue.get("fix_flow"), dict):
            fail(f"issue translation {issue_key} must define title and fix_flow")
    text = json.dumps(translations).lower()
    for phrase in (
        "fully close the weber app",
        "initial setup requires working internet access",
        "active proxy",
        "phone_and_home_assistant",
    ):
        if phrase not in text:
            fail(f"setup copy is missing required guidance: {phrase}")


def check_python() -> None:
    for path in sorted(INTEGRATION.glob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True)
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, py_compile.PyCompileError) as exc:
            fail(f"{path.relative_to(ROOT)} does not compile: {exc}")


def check_privacy_and_scope() -> None:
    diagnostics = (INTEGRATION / "diagnostics.py").read_text(encoding="utf-8")
    for private_key in (
        "CONF_CLOUD_PASSWORD",
        '"companion_private_key"',
        '"companion_public_key"',
    ):
        if private_key not in diagnostics:
            fail(f"diagnostics do not redact {private_key}")
    constants = (INTEGRATION / "const.py").read_text(encoding="utf-8")
    for removed in ("CONF_COMPANION_PRIVATE_KEY", "CONF_COMPANION_PUBLIC_KEY"):
        if removed in constants:
            fail(f"transient pairing material must not have a persisted constant: {removed}")
    bluetooth = (INTEGRATION / "bluetooth.py").read_text(encoding="utf-8")
    if "async_ble_device_from_address" not in bluetooth:
        fail("Bluetooth transport must resolve devices through Home Assistant")
    if "ble_device_callback" not in bluetooth:
        fail("Bluetooth retries must re-resolve the best adapter or proxy")
    if "async_ble_device_from_address" in (INTEGRATION / "weber_cloud.py").read_text():
        fail("cloud code must not own Bluetooth adapter selection")
    models = (INTEGRATION / "models.py").read_text(encoding="utf-8")
    for removed in ("private_key", "appliance_public_key", "verification_code"):
        if removed in models:
            fail(f"unused pairing field must not remain in runtime models: {removed}")
    cloud_client = (INTEGRATION / "weber_cloud.py").read_text(encoding="utf-8")
    if "def associate(" in cloud_client:
        fail("obsolete manual cloud-association endpoint must not ship")
    platforms = ast.literal_eval(
        next(
            line.split("=", 1)[1].strip()
            for line in (INTEGRATION / "const.py").read_text(encoding="utf-8").splitlines()
            if line.startswith("PLATFORMS:")
        )
    )
    if platforms != ("binary_sensor", "sensor"):
        fail("the integration must expose its sensor and connection platforms")
    evidence = load_json(
        ROOT / "docs" / "validation" / f"{RUNTIME_EVIDENCE_VERSION}-rc-physical.json"
    )
    evidence_text = json.dumps(evidence)
    if evidence.get("candidate") != RUNTIME_EVIDENCE_VERSION:
        fail("physical validation evidence must match the runtime evidence version")
    if evidence.get("identifiers") != "redacted":
        fail("physical validation evidence must declare identifiers redacted")
    if re.search(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b", evidence_text):
        fail("physical validation evidence must not contain MAC addresses")
    automated = load_json(
        ROOT / "docs" / "validation" / f"{RUNTIME_EVIDENCE_VERSION}-rc-automated.json"
    )
    if automated.get("candidate") != RUNTIME_EVIDENCE_VERSION:
        fail("automated validation evidence must match the runtime evidence version")
    tests = automated.get("tests")
    if (
        not isinstance(tests, dict)
        or float(tests.get("combined_statement_branch_coverage_percent", 0)) < 95
    ):
        fail("automated validation evidence must record at least 95% combined coverage")


def check_workflows() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for required in ("hassfest", "hacs/action@", "mypy", "bandit", "pip-audit"):
        if required not in ci:
            fail(f"CI is missing release gate: {required}")
    if "--cov-fail-under=95" not in ci:
        fail("CI must enforce at least 95% native integration coverage")
    if "--cov-branch" not in ci:
        fail("CI must include branch coverage in the 95% release floor")
    auto_merge = (ROOT / ".github" / "workflows" / "auto-merge.yml").read_text(encoding="utf-8")
    review_stamp = auto_merge.find("if ! stamp_review_gate success")
    fork_stop = auto_merge.find('if [[ "$is_cross_repo" == "true" ]]; then')
    auto_merge_arm = auto_merge.find('gh pr merge "$pr_ref" --auto --squash')
    if -1 in (review_stamp, fork_stop, auto_merge_arm):
        fail("auto-merge workflow is missing the fork review-gate safeguards")
    if not review_stamp < fork_stop < auto_merge_arm:
        fail("fork PRs must receive the review-gate status before auto-merge stops")
    for exact_head_guard in (
        '--arg head "$head_sha"',
        '--arg prefix "${head_sha:0:10}"',
        '--arg short_head "$short_comment_head"',
        "select(.commit_id == $head)",
        '"Reviewed commit:[^`]*`" + $head + "`"',
        "abbreviatedOid",
        '&& "${#unique_prefix}" -le 10',
        "^## Review result: No issues found",
        "^Codex Review: Didn[^A-Za-z0-9]t find any major issues",
        "codex_boilerplate",
        'normalized_body == ("## Review result: No issues found.',
        '&& "$adverse_verdicts" == "0"',
    ):
        if exact_head_guard not in auto_merge:
            fail("auto-merge workflow must require a positive exact-head verdict")
    if "looks good|lgtm|clean review" in auto_merge:
        fail("auto-merge workflow must not authorize broad clean-language substrings")
    if (ROOT / ".github" / "workflows" / "publish.yml").exists():
        fail("the native integration must not retain the add-on container publishing workflow")


def check_brand_assets() -> None:
    brand = INTEGRATION / "brand"
    expected_dimensions = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "logo.png": (640, 256),
        "logo@2x.png": (1280, 512),
    }
    for asset_name, expected in expected_dimensions.items():
        asset = brand / asset_name
        if not asset.is_file():
            fail(f"integration brand asset is missing: {asset.relative_to(ROOT)}")
        header = asset.read_bytes()[:26]
        if len(header) != 26 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            fail(f"integration brand asset is not a valid PNG: {asset.relative_to(ROOT)}")
        dimensions = struct.unpack(">II", header[16:24])
        if dimensions != expected:
            fail(f"integration brand asset {asset_name} must be {expected[0]}x{expected[1]}")
        if header[25] not in {4, 6}:
            fail(f"integration brand asset must preserve transparency: {asset_name}")
        publication_copy = ROOT / "images" / asset_name
        if not publication_copy.is_file() or publication_copy.read_bytes() != asset.read_bytes():
            fail(f"publication brand asset must match the integration asset: {asset_name}")


def main() -> int:
    check_required_files()
    check_manifest()
    check_translations()
    check_python()
    check_privacy_and_scope()
    check_workflows()
    check_brand_assets()
    print("Native integration release validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

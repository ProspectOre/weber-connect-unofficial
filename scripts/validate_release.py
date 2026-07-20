#!/usr/bin/env python3
"""Validate the 3.0 native Home Assistant integration release contract."""

from __future__ import annotations

import ast
import json
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "weber_connect"
VERSION = "3.0.0"
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
            fail(f"legacy add-on artifact must not ship in 3.0: {removed}")


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
    text = json.dumps(translations)
    for phrase in (
        "Fully close the Weber app",
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
        "CONF_COMPANION_PRIVATE_KEY",
        "CONF_COMPANION_PUBLIC_KEY",
    ):
        if private_key not in diagnostics:
            fail(f"diagnostics do not redact {private_key}")
    bluetooth = (INTEGRATION / "bluetooth.py").read_text(encoding="utf-8")
    if "async_ble_device_from_address" not in bluetooth:
        fail("Bluetooth transport must resolve devices through Home Assistant")
    if "ble_device_callback" not in bluetooth:
        fail("Bluetooth retries must re-resolve the best adapter or proxy")
    if "async_ble_device_from_address" in (INTEGRATION / "weber_cloud.py").read_text():
        fail("cloud code must not own Bluetooth adapter selection")
    platforms = ast.literal_eval(
        next(
            line.split("=", 1)[1].strip()
            for line in (INTEGRATION / "const.py").read_text(encoding="utf-8").splitlines()
            if line.startswith("PLATFORMS:")
        )
    )
    if platforms != ("sensor",):
        fail("3.0 must expose only the four probe temperature sensors")


def check_workflows() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for required in ("hassfest", "hacs/action@", "mypy", "bandit", "pip-audit"):
        if required not in ci:
            fail(f"CI is missing release gate: {required}")
    if "--cov-fail-under=95" not in ci:
        fail("CI must enforce at least 95% native integration coverage")
    if (ROOT / ".github" / "workflows" / "publish.yml").exists():
        fail("3.0 must not retain the add-on container publishing workflow")


def check_brand_assets() -> None:
    brand = INTEGRATION / "brand"
    for asset_name in ("icon.png", "logo.png"):
        asset = brand / asset_name
        if not asset.is_file():
            fail(f"integration brand asset is missing: {asset.relative_to(ROOT)}")


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

#!/usr/bin/env python3
"""Resolve and run the repository's explicit Home Assistant test lanes."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / ".github/ha-test-matrix.json"
PYPI = "https://pypi.org/pypi"
CORE_RAW = "https://raw.githubusercontent.com/home-assistant/core"
TRANSPORT_PACKAGES = {
    "bleak",
    "bleak-retry-connector",
    "bluetooth-adapters",
    "bluetooth-auto-recovery",
    "bluetooth-data-tools",
    "dbus-fast",
    "habluetooth",
    "aiousbwatcher",
    "serialx",
    "aioesphomeapi",
}
LATEST_SMOKE_PACKAGES = {"aiohasupervisor"}
PACKAGE_KEYS = {
    "homeassistant": "homeassistant",
    "pytest_homeassistant_custom_component": "pytest-homeassistant-custom-component",
    "pytest": "pytest",
    "pytest_asyncio": "pytest-asyncio",
    "pytest_timeout": "pytest-timeout",
    "pytest_cov": "pytest-cov",
}


class CompatibilityError(ValueError):
    """Raised when upstream compatibility metadata cannot be trusted."""


def _version_key(version: str) -> tuple[int, ...]:
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise CompatibilityError(f"Invalid stable version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def _json_url(url: str) -> dict[str, Any]:
    _validate_metadata_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "weber-connect-ha-compatibility/1"}
    )
    try:
        # This endpoint is restricted to HTTPS PyPI or tagged HA core manifests by _validate_metadata_url.
        with urllib.request.urlopen(  # nosec B310
            request, timeout=20
        ) as response:
            data = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise CompatibilityError(f"Could not read official metadata at {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise CompatibilityError(f"Malformed metadata at {url}: expected an object")
    return data


def _validate_metadata_url(url: str) -> None:
    """Allow only known public metadata endpoints and safe version/path segments."""
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise CompatibilityError("Metadata URL has an invalid port") from exc
    if parsed.scheme != "https" or parsed.username or parsed.password or port:
        raise CompatibilityError("Metadata URL must use HTTPS without userinfo or a custom port")
    if parsed.query or parsed.fragment:
        raise CompatibilityError("Metadata URL cannot contain a query or fragment")
    pypi_path = re.fullmatch(
        r"/pypi/(homeassistant|pytest-homeassistant-custom-component)(?:/([0-9]+(?:\.[0-9]+){1,3}))?/json",
        parsed.path,
    )
    if parsed.hostname == "pypi.org" and pypi_path:
        if pypi_path.group(2):
            _version_key(pypi_path.group(2))
        return
    core_path = re.fullmatch(
        r"/home-assistant/core/([0-9]+(?:\.[0-9]+){1,3})/homeassistant/components/(bluetooth|usb|esphome|hassio)/manifest\.json",
        parsed.path,
    )
    if parsed.hostname == "raw.githubusercontent.com" and core_path:
        _version_key(core_path.group(1))
        return
    raise CompatibilityError("Metadata URL is outside the approved PyPI and HA core sources")


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompatibilityError(f"Could not read matrix config {path}: {exc}") from exc
    if (
        not isinstance(config, dict)
        or type(config.get("schema_version")) is not int
        or config.get("schema_version") != 1
        or set(config) != {"schema_version", "full_lanes", "latest_smoke"}
    ):
        raise CompatibilityError("Matrix config must be an object with schema_version 1")
    lanes = config.get("full_lanes")
    if not isinstance(lanes, list) or len(lanes) != 2:
        raise CompatibilityError("Matrix must define exactly minimum and current full lanes")
    if not all(isinstance(lane, dict) and isinstance(lane.get("id"), str) for lane in lanes):
        raise CompatibilityError("Every full lane must be an object with a string ID")
    if {lane["id"] for lane in lanes} != {"minimum", "current"}:
        raise CompatibilityError("Full lane IDs must be minimum and current")
    latest = config.get("latest_smoke")
    if not isinstance(latest, dict) or latest.get("id") != "latest":
        raise CompatibilityError("Latest smoke lane must have id 'latest'")
    ids: set[str] = set()
    for lane in [*lanes, latest]:
        if not isinstance(lane, dict):
            raise CompatibilityError("Every lane must be an object")
        for key in ("id", "label", "homeassistant", "transport_requirements"):
            if key not in lane:
                raise CompatibilityError(f"Lane is missing {key!r}")
        if not isinstance(lane["id"], str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,31}", lane["id"]
        ):
            raise CompatibilityError("Lane IDs must be safe lowercase identifiers")
        if lane["id"] in ids:
            raise CompatibilityError(f"Duplicate lane id: {lane['id']!r}")
        ids.add(lane["id"])
        if (
            not isinstance(lane["label"], str)
            or not lane["label"].strip()
            or len(lane["label"]) > 100
        ):
            raise CompatibilityError(f"Lane {lane['id']} has an invalid label")
        _version_key(lane["homeassistant"])
        requirements = lane["transport_requirements"]
        if not isinstance(requirements, dict):
            raise CompatibilityError(f"Lane {lane['id']} has malformed transport requirements")
        normalized = [
            re.sub(r"[-_.]+", "-", key).lower() for key in requirements if isinstance(key, str)
        ]
        if len(normalized) != len(requirements) or len(set(normalized)) != len(normalized):
            raise CompatibilityError(f"Lane {lane['id']} has invalid or duplicate package names")
        if set(normalized) != TRANSPORT_PACKAGES:
            raise CompatibilityError(
                f"Lane {lane['id']} transport package set is incomplete or unknown"
            )
        for package, version in requirements.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", package):
                raise CompatibilityError(f"Lane {lane['id']} has unsafe package name {package!r}")
            _version_key(version)
        expected_fields = (
            {
                "id",
                "label",
                "homeassistant",
                "pytest_homeassistant_custom_component",
                "pytest",
                "pytest_asyncio",
                "pytest_timeout",
                "pytest_cov",
                "transport_requirements",
            }
            if lane["id"] in {"minimum", "current"}
            else {
                "id",
                "label",
                "homeassistant",
                "pytest",
                "pytest_asyncio",
                "pytest_cov",
                "extra_requirements",
                "transport_requirements",
            }
        )
        if set(lane) != expected_fields:
            raise CompatibilityError(f"Lane {lane['id']} has unknown or missing fields")
        extras = lane.get("extra_requirements", {})
        if lane["id"] == "latest":
            if not isinstance(extras, dict) or set(extras) != LATEST_SMOKE_PACKAGES:
                raise CompatibilityError(
                    "Latest smoke lane must pin its required HA component helpers"
                )
            for package, version in extras.items():
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", package):
                    raise CompatibilityError(
                        f"Latest lane has unsafe extra package name {package!r}"
                    )
                _version_key(version)
        for field in expected_fields - {
            "id",
            "label",
            "transport_requirements",
            "extra_requirements",
        }:
            _version_key(lane[field])
    return config


def _pypi_project(project: str) -> dict[str, Any]:
    return _json_url(f"{PYPI}/{project}/json")


def _requirements_dist(info: dict[str, Any]) -> list[str]:
    values = info.get("requires_dist")
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise CompatibilityError("PyPI release has malformed requires_dist metadata")
    return values


def _exact_requirement(requirements: list[str], project: str) -> str | None:
    normalized = re.sub(r"[-_.]+", "-", project).lower()
    matches = []
    for requirement in requirements:
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^;\s]+)(?:\s*;.*)?$", requirement)
        if match and re.sub(r"[-_.]+", "-", match.group(1)).lower() == normalized:
            version = match.group(2)
            _version_key(version)
            matches.append(version)
    if len(set(matches)) > 1:
        raise CompatibilityError(f"Conflicting exact {project} requirements in PyPI metadata")
    return matches[0] if matches else None


def _paired_latest() -> tuple[str, str, str]:
    ha = _pypi_project("homeassistant")
    ha_versions = sorted(
        (version for version in ha.get("releases", {}) if _is_stable(version)),
        key=_version_key,
    )
    latest_ha = ha_versions[-1] if ha_versions else None
    if latest_ha is None:
        raise CompatibilityError("PyPI returned no stable Home Assistant releases")

    plugin = _pypi_project("pytest-homeassistant-custom-component")
    releases = plugin.get("releases")
    if not isinstance(releases, dict):
        raise CompatibilityError("PyPI plugin metadata has no release map")
    plugin_versions = sorted((v for v in releases if _is_stable(v)), key=_version_key, reverse=True)
    for plugin_version in plugin_versions:
        release_data = _json_url(
            f"{PYPI}/pytest-homeassistant-custom-component/{plugin_version}/json"
        )
        info = release_data.get("info")
        if not isinstance(info, dict):
            raise CompatibilityError(f"Malformed plugin release metadata for {plugin_version}")
        bound_ha = _exact_requirement(_requirements_dist(info), "homeassistant")
        if bound_ha and bound_ha in ha_versions:
            return latest_ha, plugin_version, bound_ha
    raise CompatibilityError("No stable pytest HA plugin release has an exact stable HA binding")


def _is_stable(version: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+){1,3}", version))


def _manifest_requirements(version: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for component in ("bluetooth", "usb", "esphome"):
        url = f"{CORE_RAW}/{version}/homeassistant/components/{component}/manifest.json"
        manifest = _json_url(url)
        requirements = manifest.get("requirements")
        if not isinstance(requirements, list):
            raise CompatibilityError(f"Malformed {component} requirements in HA {version} manifest")
        for requirement in requirements:
            if not isinstance(requirement, str):
                raise CompatibilityError(
                    f"Malformed {component} requirement in HA {version} manifest"
                )
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9]+(?:\.[0-9]+){1,3})", requirement)
            if not match:
                continue
            package, pin = match.groups()
            normalized = re.sub(r"[-_.]+", "-", package).lower()
            if normalized in {
                "bleak",
                "bleak-retry-connector",
                "bluetooth-adapters",
                "bluetooth-auto-recovery",
                "bluetooth-data-tools",
                "dbus-fast",
                "habluetooth",
                "aiousbwatcher",
                "serialx",
                "aioesphomeapi",
            }:
                if package in merged and merged[package] != pin:
                    raise CompatibilityError(f"Conflicting official manifest pins for {package}")
                merged[package] = pin
    expected = {
        "bleak",
        "bleak-retry-connector",
        "bluetooth-adapters",
        "bluetooth-auto-recovery",
        "bluetooth-data-tools",
        "dbus-fast",
        "habluetooth",
        "aiousbwatcher",
        "serialx",
        "aioesphomeapi",
    }
    normalized = {re.sub(r"[-_.]+", "-", name).lower() for name in merged}
    if normalized != expected:
        raise CompatibilityError(
            f"HA {version} manifests missing required transport pins: {sorted(expected - normalized)}"
        )
    return merged


def _manifest_extra_requirements(version: str) -> dict[str, str]:
    manifest = _json_url(f"{CORE_RAW}/{version}/homeassistant/components/hassio/manifest.json")
    requirements = manifest.get("requirements")
    if not isinstance(requirements, list):
        raise CompatibilityError(f"Malformed hassio requirements in HA {version} manifest")
    extras: dict[str, str] = {}
    for requirement in requirements:
        if not isinstance(requirement, str):
            raise CompatibilityError(f"Malformed hassio requirement in HA {version} manifest")
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9]+(?:\.[0-9]+){1,3})", requirement)
        if match and match.group(1).lower() == "aiohasupervisor":
            extras[match.group(1)] = match.group(2)
    if set(extras) != LATEST_SMOKE_PACKAGES:
        raise CompatibilityError(f"HA {version} hassio manifest has no exact aiohasupervisor pin")
    return extras


def _write_github_output(path: Path, name: str, value: str) -> None:
    delimiter = "MATRIX_" + re.sub(r"[^A-Za-z0-9]", "", name).upper()
    with path.open("a", encoding="utf-8") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def command_matrix(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    note = "Full native suite runs on minimum and current exact HA/plugin pairs; latest stable runs import and pure-protocol smoke only."
    if args.refresh:
        latest_ha, plugin_version, plugin_ha = _paired_latest()
        config["latest_smoke"]["homeassistant"] = latest_ha
        config["latest_smoke"]["extra_requirements"] = _manifest_extra_requirements(latest_ha)
        current = next((lane for lane in config["full_lanes"] if lane["id"] == "current"), None)
        minimum = next((lane for lane in config["full_lanes"] if lane["id"] == "minimum"), None)
        if current is None or minimum is None:
            raise CompatibilityError("Refresh requires full lane ids minimum and current")
        if latest_ha == plugin_ha:
            current["homeassistant"] = plugin_ha
            current["pytest_homeassistant_custom_component"] = plugin_version
            current["transport_requirements"] = _manifest_requirements(plugin_ha)
            note = f"Full suite covers latest stable HA {latest_ha} with exact plugin {plugin_version}."
        else:
            current["homeassistant"] = plugin_ha
            current["pytest_homeassistant_custom_component"] = plugin_version
            current["transport_requirements"] = _manifest_requirements(plugin_ha)
            config["latest_smoke"]["transport_requirements"] = _manifest_requirements(latest_ha)
            note = (
                f"Plugin {plugin_version} is bound to HA {plugin_ha}; latest stable HA {latest_ha} "
                "is only covered by import and pure-protocol smoke until an exact plugin binding exists."
            )
    for lane in [*config["full_lanes"], config["latest_smoke"]]:
        if args.refresh:
            lane["transport_requirements"] = _manifest_requirements(lane["homeassistant"])
    full = json.dumps(
        {
            "include": [
                {"lane": lane["id"], "label": lane["label"]} for lane in config["full_lanes"]
            ]
        },
        separators=(",", ":"),
    )
    latest = json.dumps(
        {
            "include": [
                {
                    "lane": config["latest_smoke"]["id"],
                    "label": config["latest_smoke"]["label"],
                }
            ]
        },
        separators=(",", ":"),
    )
    if args.github_output:
        _write_github_output(args.github_output, "full_matrix", full)
        _write_github_output(args.github_output, "latest_matrix", latest)
        _write_github_output(args.github_output, "coverage_note", note)
    else:
        print(
            json.dumps(
                {"full_matrix": full, "latest_matrix": latest, "coverage_note": note}, indent=2
            )
        )
    if args.resolved_config:
        args.resolved_config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    if args.coverage_file:
        args.coverage_file.write_text(note + "\n", encoding="utf-8")
    if args.refresh and args.config != DEFAULT_CONFIG:
        args.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return 0


def _lane(config: dict[str, Any], lane_id: str) -> dict[str, Any]:
    for lane in [*config["full_lanes"], config["latest_smoke"]]:
        if lane["id"] == lane_id:
            return lane
    raise CompatibilityError(f"Unknown lane {lane_id!r}")


def command_requirements(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    lane = _lane(config, args.lane)
    for key, project in PACKAGE_KEYS.items():
        if key in lane:
            print(f"{project}=={lane[key]}")
    for package, version in lane.get("extra_requirements", {}).items():
        print(f"{package}=={version}")
    for package, version in lane["transport_requirements"].items():
        print(f"{package}=={version}")
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    lane = _lane(config, "latest")
    import_dir = ROOT / "custom_components/weber_connect"
    modules = sorted(path.stem for path in import_dir.glob("*.py") if path.name != "__init__.py")
    import importlib

    sys.path.insert(0, str(ROOT))

    from importlib.metadata import version as installed_version

    from homeassistant.helpers import device_registry, entity_registry

    actual_version = installed_version("homeassistant")
    if actual_version != lane["homeassistant"]:
        raise CompatibilityError(
            f"Selected lane is HA {lane['homeassistant']}, but installed HA is {actual_version}"
        )
    if not callable(device_registry.async_get) or not callable(entity_registry.async_get):
        raise CompatibilityError("HA device and entity registry async_get APIs are unavailable")
    if "er.async_get(" not in (import_dir / "entity.py").read_text(encoding="utf-8"):
        raise CompatibilityError(
            "Entity implementation does not use the supported entity registry helper"
        )
    coordinator_source = (import_dir / "coordinator.py").read_text(encoding="utf-8")
    if "dr.async_get(" not in coordinator_source:
        raise CompatibilityError("Coordinator does not use the supported device registry helper")
    print(f"Import smoke for HA {lane['homeassistant']}: {len(modules)} integration modules")
    for module in modules:
        importlib.import_module(f"custom_components.weber_connect.{module}")
    import pytest

    pytest_result = pytest.main(
        [
            "-q",
            "--override-ini=addopts=",
            str(ROOT / "tests_native/test_saber_frames.py"),
            str(ROOT / "tests_native/test_silabs_secure.py"),
        ]
    )
    print(
        "Latest lane scope: integration import and pure protocol tests only; no full HA suite or coverage claim."
    )
    return int(pytest_result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="matrix JSON (default: repository config)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    matrix = commands.add_parser("matrix", help="print workflow matrix and scope note")
    matrix.add_argument(
        "--refresh",
        action="store_true",
        help="refresh stable HA/plugin pair and official transport pins",
    )
    matrix.add_argument("--github-output", type=Path, help="append workflow outputs to this path")
    matrix.add_argument(
        "--resolved-config", type=Path, help="write the selected config copy to this path"
    )
    matrix.add_argument(
        "--coverage-file", type=Path, help="write the human-readable suite scope note to this path"
    )
    matrix.set_defaults(run=command_matrix)
    requirements = commands.add_parser("requirements", help="print exact pip pins for a lane")
    requirements.add_argument("--lane", choices=("minimum", "current", "latest"), required=True)
    requirements.set_defaults(run=command_requirements)
    smoke = commands.add_parser("smoke", help="run latest import and pure-protocol smoke tests")
    smoke.set_defaults(run=command_smoke)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.run(args)
    except CompatibilityError as exc:
        print(f"HA compatibility check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate the public Home Assistant add-on repository."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "weber_connect_ble"
VERSION_RE = re.compile(r'^VERSION = "([^"]+)"$', re.MULTILINE)

# Known private identifiers are stored only as SHA-256 hashes of their
# normalized (lowercased) form so that neither this script nor its tests ever
# embed the real owner's BLE addresses or CoreBluetooth UUID in plaintext.
# Scanning re-hashes MAC- and UUID-shaped tokens and compares against this set.
FORBIDDEN_IDENTIFIER_HASHES = {
    "0aff84b31851bad21cb6a367227758a83970c0e88cfe691fccb8e58c092a0f77": "private BLE address",
    "584786afc63171b96721a942307cfb25a3c7b38d188058aa9beb52f31c75e660": "private BLE address",
    "3c4ee3f33fec2d5683c734b603108c3a678d64e69811cdd29672b4252ff41db2": "private CoreBluetooth address",
}

# Documentation/synthetic values that are explicitly permitted to appear.
ALLOWED_MAC_ADDRESSES = {
    "aa:bb:cc:dd:ee:ff",
    "00:11:22:33:44:55",
}
# RFC 5737 documentation ranges, loopback, and the unspecified address.
ALLOWED_IP_NETWORKS = [
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/32"),
    # Home Assistant Supervisor's fixed internal ingress network (hassio).
    # These are documented infrastructure addresses, not private user data.
    ipaddress.ip_network("172.30.32.0/23"),
]

# A maximal run of colon-separated hex octets; only exactly six octets is a MAC.
COLON_HEX_RE = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:)+[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
    r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])"
)
# Maximal dotted-decimal runs; ipaddress validation rejects anything that is
# not a well-formed IPv4 quad (version strings, five-octet tokens, >255, ...).
IPV4_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")

TEXT_EXTENSIONS = {
    ".md",
    ".yaml",
    ".yml",
    ".py",
    ".sh",
    ".txt",
    ".dockerignore",
    ".gitignore",
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        result = subprocess.run(
            [
                "ruby",
                "-ryaml",
                "-rjson",
                "-e",
                "puts JSON.generate(YAML.load_file(ARGV[0]))",
                str(path),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        data = json.loads(result.stdout)
    else:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        fail(f"{path.relative_to(ROOT)} did not parse as a YAML object")
    return data


def check_required_files() -> None:
    required = [
        "repository.yaml",
        "README.md",
        "ARCHITECTURE.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "weber_connect_ble/config.yaml",
        "weber_connect_ble/Dockerfile",
        "weber_connect_ble/run.sh",
        "weber_connect_ble/icon.png",
        "weber_connect_ble/logo.png",
        "weber_connect_ble/translations/en.yaml",
        "weber_connect_ble/README.md",
        "weber_connect_ble/DOCS.md",
        "weber_connect_ble/CHANGELOG.md",
        "weber_connect_ble/app/saber_frames.py",
        "weber_connect_ble/app/weber_ble_pair.py",
        "weber_connect_ble/app/weber_ble_scan.py",
        "weber_connect_ble/app/weber_cloud.py",
        "weber_connect_ble/app/weber_status_bridge.py",
        "weber_connect_ble/app/weber_panel.py",
        "weber_connect_ble/app/weber_http.py",
        "weber_connect_ble/app/weber_mqtt.py",
        "weber_connect_ble/app/weber_persistence.py",
        "weber_connect_ble/app/weber_runtime.py",
        "weber_connect_ble/app/static/index.html",
        "weber_connect_ble/app/requirements.in",
        "weber_connect_ble/app/requirements.txt",
        "requirements-dev.in",
        "requirements-dev.txt",
        "pyproject.toml",
        "tests/test_bridge_contracts.py",
    ]
    for relative in required:
        if not (ROOT / relative).exists():
            fail(f"missing required file: {relative}")


def check_yaml() -> None:
    repository = load_yaml(ROOT / "repository.yaml")
    addon = load_yaml(ADDON / "config.yaml")

    for key in ("name", "url", "maintainer"):
        if not repository.get(key):
            fail(f"repository.yaml missing {key}")

    for key in ("name", "slug", "version", "description", "arch", "schema", "options"):
        if not addon.get(key):
            fail(f"config.yaml missing {key}")

    if addon["slug"] != "weber_connect_ble":
        fail("config.yaml slug must be weber_connect_ble")
    if "host_dbus" not in addon:
        fail("config.yaml must request host_dbus for BlueZ BLE access")
    if "mqtt:want" not in addon.get("services", []):
        fail("config.yaml should declare mqtt:want service")
    if addon.get("ingress") is not True:
        fail("config.yaml must enable ingress for the panel UI")
    if addon.get("ingress_port") != 8099:
        fail("config.yaml must expose the panel on ingress_port 8099")
    if addon["options"].get("log_level") != "info":
        fail("config.yaml should default log_level to info")
    if set(addon["schema"]) != {"log_level", "mqtt"}:
        fail("config.yaml schema must stay minimal: log_level and mqtt only")


def require_translated_option(row: object, path: str) -> None:
    if not isinstance(row, dict):
        fail(f"{path} translation must be an object")
    for key in ("name", "description"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            fail(f"{path} translation missing {key}")


def check_translations() -> None:
    addon = load_yaml(ADDON / "config.yaml")
    translations = load_yaml(ADDON / "translations/en.yaml")
    configuration = translations.get("configuration")
    if not isinstance(configuration, dict):
        fail("translations/en.yaml missing configuration object")

    schema = addon["schema"]
    for key, value in schema.items():
        row = configuration.get(key)
        require_translated_option(row, f"configuration.{key}")
        if isinstance(value, dict):
            fields = row.get("fields")
            if not isinstance(fields, dict):
                fail(f"configuration.{key} missing fields translations")
            for nested_key in value:
                require_translated_option(
                    fields.get(nested_key),
                    f"configuration.{key}.fields.{nested_key}",
                )


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        fail(f"{path.relative_to(ROOT)} is not a PNG file")
    if data[12:16] != b"IHDR":
        fail(f"{path.relative_to(ROOT)} is missing a PNG IHDR chunk")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def check_artwork() -> None:
    expected_dimensions = {
        ADDON / "icon.png": (128, 128),
        ADDON / "logo.png": (250, 100),
    }
    for path, expected in expected_dimensions.items():
        actual = png_dimensions(path)
        if actual != expected:
            fail(
                f"{path.relative_to(ROOT)} has dimensions {actual[0]}x{actual[1]}, "
                f"expected {expected[0]}x{expected[1]}"
            )


def check_dockerfile() -> None:
    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    for architecture in ("amd64", "aarch64"):
        if not re.search(
            rf"home-assistant/{architecture}-base:3\.21@sha256:[0-9a-f]{{64}} AS base-{architecture}",
            dockerfile,
        ):
            fail(f"Dockerfile must pin the {architecture} base image by digest")
    if "FROM base-${BUILD_ARCH}" not in dockerfile:
        fail("Dockerfile must select the pinned base stage using BUILD_ARCH")
    if (ADDON / "build.yaml").exists():
        fail("build.yaml is deprecated; base images come from the BUILD_FROM build arg")
    for expected in (
        'LABEL io.hass.version="${BUILD_VERSION}"',
        'io.hass.type="app"',
        'io.hass.arch="${BUILD_ARCH}"',
    ):
        if expected not in dockerfile:
            fail(f"Dockerfile missing Home Assistant image label: {expected}")


def check_versions() -> None:
    addon = load_yaml(ADDON / "config.yaml")
    bridge_source = (ADDON / "app/weber_status_bridge.py").read_text(encoding="utf-8")
    match = VERSION_RE.search(bridge_source)
    if not match:
        fail("weber_status_bridge.py is missing VERSION")
    if addon["version"] != match.group(1):
        fail("config.yaml version does not match bridge VERSION")
    changelog = (ADDON / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {addon['version']}" not in changelog:
        fail("CHANGELOG.md missing current version section")


def check_python() -> None:
    for path in sorted((ADDON / "app").glob("*.py")):
        py_compile.compile(str(path), doraise=True)


def check_unit_tests() -> None:
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests")],
        check=True,
        cwd=ROOT,
    )


def check_shell() -> None:
    run_sh = ADDON / "run.sh"
    if not run_sh.stat().st_mode & 0o111:
        fail("weber_connect_ble/run.sh must be executable")
    subprocess.run(["bash", "-n", str(run_sh)], check=True)


def check_secret_handling() -> None:
    run_text = (ADDON / "run.sh").read_text(encoding="utf-8")
    if "export MQTT_USERNAME MQTT_PASSWORD" in run_text:
        fail("run.sh must not export MQTT credentials into the long-running process")
    if "--mqtt-password" in run_text:
        fail("run.sh must pass MQTT credentials by private file, not command-line arguments")
    if "unset MQTT_USERNAME MQTT_PASSWORD" not in run_text:
        fail("run.sh must clear MQTT credential shell variables before starting the bridge")


def check_ci_coverage() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for expected in (
        "weber_status_bridge.py --help",
        "weber_ble_scan.py --help",
        "weber_ble_pair.py --help",
        "weber_panel.py --help",
        "--build-arg BUILD_ARCH",
        "--build-arg BUILD_VERSION",
        "platform: linux/amd64",
        "platform: linux/arm64",
        "ruff check",
        "mypy",
        "coverage report",
        "pip-audit",
    ):
        if expected not in workflow:
            fail(f"CI workflow missing expected coverage: {expected}")


def check_dependency_locks() -> None:
    requirements_in = (ADDON / "app/requirements.in").read_text(encoding="utf-8")
    requirements_lock = (ADDON / "app/requirements.txt").read_text(encoding="utf-8")
    direct_requirements = [
        line.strip()
        for line in requirements_in.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for requirement in direct_requirements:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", requirement):
            fail(f"runtime dependency must be exactly pinned: {requirement}")
        if requirement.lower() not in requirements_lock.lower():
            fail(f"runtime dependency lock is missing {requirement}")
    if "--hash=sha256:" not in requirements_lock:
        fail("runtime dependency lock must include package hashes")

    dev_requirements_in = (ROOT / "requirements-dev.in").read_text(encoding="utf-8")
    dev_requirements_lock = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    dev_direct = [
        line.strip()
        for line in dev_requirements_in.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for requirement in dev_direct:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", requirement):
            fail(f"development dependency must be exactly pinned: {requirement}")
        if requirement.lower() not in dev_requirements_lock.lower():
            fail(f"development dependency lock is missing {requirement}")
    if "--hash=sha256:" not in dev_requirements_lock:
        fail("development dependency lock must include package hashes")

    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if "--require-hashes -r requirements-dev.txt" not in ci_workflow:
        fail("CI must install development dependencies with --require-hashes")

    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    if "--require-hashes -r /tmp/requirements.txt" not in dockerfile:
        fail("Dockerfile must enforce hashes from the runtime dependency lock")


def should_scan(path: Path) -> bool:
    # The scanner deliberately scans itself: identifiers live here only as
    # hashes, so validate_release.py must survive its own gate.
    if ".git" in path.parts:
        return False
    if any(part in {".venv", "venv", ".mypy_cache", ".ruff_cache", "__pycache__"} for part in path.parts):
        return False
    if path.suffix in TEXT_EXTENSIONS:
        return True
    return path.name in {".dockerignore", ".gitignore", "Dockerfile"}


def _iter_mac_addresses(text: str) -> "list[str]":
    macs = []
    for match in COLON_HEX_RE.finditer(text):
        token = match.group(0)
        if token.count(":") == 5:  # exactly six octets is a MAC address
            macs.append(token.lower())
    return macs


def _iter_ipv4_addresses(text: str) -> "list[str]":
    addresses = []
    for match in IPV4_RE.finditer(text):
        try:
            addresses.append(ipaddress.ip_address(match.group(0)))
        except ValueError:
            continue
    return addresses


def check_no_private_material() -> None:
    forbidden_paths = ["weber_probe", "secure", "captures"]
    for relative in forbidden_paths:
        if (ROOT / relative).exists():
            fail(f"private runtime directory must not exist in release repo: {relative}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or not should_scan(path):
            continue
        relative = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore")

        # 1. Known private identifiers (by hash), covering MAC and UUID tokens.
        for token in (*_iter_mac_addresses(text), *(m.group(0).lower() for m in UUID_RE.finditer(text))):
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if digest in FORBIDDEN_IDENTIFIER_HASHES:
                fail(f"{FORBIDDEN_IDENTIFIER_HASHES[digest]} found in {relative}")

        # 2. General privacy gate: any MAC-shaped token outside the
        #    documentation/synthetic allowlist is treated as a real address.
        for mac in _iter_mac_addresses(text):
            if mac not in ALLOWED_MAC_ADDRESSES:
                fail(f"non-documentation MAC address {mac} found in {relative}")

        # 3. General privacy gate: any IPv4 outside documentation ranges.
        for address in _iter_ipv4_addresses(text):
            if not any(address in network for network in ALLOWED_IP_NETWORKS):
                fail(f"non-documentation IPv4 address {address} found in {relative}")


def main() -> int:
    check_required_files()
    check_yaml()
    check_translations()
    check_artwork()
    check_dockerfile()
    check_versions()
    check_python()
    check_unit_tests()
    check_shell()
    check_secret_handling()
    check_ci_coverage()
    check_dependency_locks()
    check_no_private_material()
    print("Release validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

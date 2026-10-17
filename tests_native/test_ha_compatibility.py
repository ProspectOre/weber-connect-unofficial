"""Tests for the explicitly bounded HA compatibility matrix resolver."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import ha_compatibility as compat


class MatrixConfigTests(unittest.TestCase):
    def test_config_keeps_minimum_and_current_transport_pins_separate(self) -> None:
        config = compat.load_config()
        minimum, current = config["full_lanes"]
        self.assertEqual(
            (minimum["homeassistant"], minimum["pytest_homeassistant_custom_component"]),
            ("2026.7.2", "0.13.346"),
        )
        self.assertEqual(
            (current["homeassistant"], current["pytest_homeassistant_custom_component"]),
            ("2026.10.1", "0.13.372"),
        )
        self.assertEqual(minimum["transport_requirements"]["habluetooth"], "6.26.5")
        self.assertEqual(current["transport_requirements"]["habluetooth"], "7.1.2")
        self.assertEqual(minimum["transport_requirements"]["serialx"], "1.8.2")
        self.assertEqual(current["transport_requirements"]["serialx"], "1.11.0")

    def test_rejects_malformed_matrix_config(self) -> None:
        with self.assertRaises(compat.CompatibilityError):
            compat.load_config(Path("/does/not/exist/matrix.json"))
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "matrix.json"
            malformed.write_text("not json", encoding="utf-8")
            with self.assertRaises(compat.CompatibilityError):
                compat.load_config(malformed)

    def test_rejects_requirement_injection_in_matrix(self) -> None:
        config = json.loads(compat.DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config["full_lanes"][0]["transport_requirements"]["serialx"] = (
            "1.8.2;--extra-index-url=https://bad.invalid"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(compat.CompatibilityError):
                compat.load_config(path)

    def test_rejects_unknown_requirement_package(self) -> None:
        config = json.loads(compat.DEFAULT_CONFIG.read_text(encoding="utf-8"))
        config["full_lanes"][0]["transport_requirements"]["bad-package"] = "1.0.0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(compat.CompatibilityError):
                compat.load_config(path)

    def test_requirements_prints_lane_specific_exact_pins(self) -> None:
        config = compat.load_config()
        minimum = compat._lane(config, "minimum")
        current = compat._lane(config, "current")
        self.assertEqual(minimum["transport_requirements"]["serialx"], "1.8.2")
        self.assertEqual(current["transport_requirements"]["serialx"], "1.11.0")
        self.assertNotIn("pytest_homeassistant_custom_component", compat._lane(config, "latest"))


class UpstreamResolutionTests(unittest.TestCase):
    def test_metadata_url_allowlist_accepts_official_sources_only(self) -> None:
        compat._validate_metadata_url("https://pypi.org/pypi/homeassistant/2026.10.2/json")
        compat._validate_metadata_url(
            "https://raw.githubusercontent.com/home-assistant/core/2026.10.2/homeassistant/components/bluetooth/manifest.json"
        )
        for url in (
            "http://pypi.org/pypi/homeassistant/json",
            "https://evil.invalid/pypi/homeassistant/json",
            "https://pypi.org/pypi/homeassistant/json?next=https://evil.invalid",
            "https://pypi.org:444/pypi/homeassistant/json",
            "https://raw.githubusercontent.com/home-assistant/core/main/homeassistant/components/bluetooth/manifest.json",
        ):
            with self.subTest(url=url), self.assertRaises(compat.CompatibilityError):
                compat._validate_metadata_url(url)

    def test_plugin_lag_keeps_latest_outside_full_test_lane(self) -> None:
        def pypi(project: str) -> dict[str, object]:
            if project == "homeassistant":
                return {"releases": {"2026.10.1": [], "2026.10.2": [], "2026.11.0b1": []}}
            return {"releases": {"0.13.372": [], "0.13.373": []}}

        def release(url: str) -> dict[str, object]:
            version = url.rsplit("/", 2)[-2]
            bound = "2026.10.1" if version == "0.13.373" else "2026.10.0"
            return {"info": {"requires_dist": [f"homeassistant=={bound}"]}}

        with (
            patch.object(compat, "_pypi_project", side_effect=pypi),
            patch.object(compat, "_json_url", side_effect=release),
        ):
            self.assertEqual(compat._paired_latest(), ("2026.10.2", "0.13.373", "2026.10.1"))

    def test_rejects_conflicting_exact_plugin_bindings(self) -> None:
        with self.assertRaises(compat.CompatibilityError):
            compat._exact_requirement(
                ["homeassistant==2026.10.1", "HomeAssistant==2026.10.2"], "homeassistant"
            )

    def test_rejects_non_version_plugin_binding(self) -> None:
        with self.assertRaises(compat.CompatibilityError):
            compat._exact_requirement(["homeassistant==not-a-version"], "homeassistant")

    def test_rejects_malformed_upstream_manifest_requirements(self) -> None:
        empty_manifest = {"requirements": []}
        with patch.object(compat, "_json_url", return_value=empty_manifest):
            with self.assertRaisesRegex(
                compat.CompatibilityError, "missing required transport pins"
            ):
                compat._manifest_requirements("2026.10.1")

    def test_network_failure_is_reported_as_compatibility_error(self) -> None:
        with patch.object(compat.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(
                compat.CompatibilityError, "Could not read official metadata"
            ):
                compat._json_url("https://pypi.org/pypi/homeassistant/json")


if __name__ == "__main__":
    unittest.main()

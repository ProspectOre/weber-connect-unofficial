from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "weber_connect_ble" / "app" / "static" / "index.html"


class PanelParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.scripts: list[str] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, {key: value or "" for key, value in attrs}))
        if tag == "script":
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None


class PanelUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = PANEL.read_text(encoding="utf-8")
        cls.parser = PanelParser()
        cls.parser.feed(cls.html)

    def test_document_has_unique_ids_and_valid_local_targets(self) -> None:
        identifiers = [attrs["id"] for _, attrs in self.parser.elements if "id" in attrs]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        available = set(identifiers)
        for _, attrs in self.parser.elements:
            for attribute in ("aria-controls", "aria-labelledby"):
                for target in attrs.get(attribute, "").split():
                    self.assertIn(target, available, f"missing {attribute} target {target}")
            href = attrs.get("href", "")
            if href.startswith("#") and len(href) > 1:
                self.assertIn(href[1:], available)

    def test_accessible_landmarks_dialogs_and_live_feedback_are_present(self) -> None:
        by_id = {
            attrs["id"]: (tag, attrs)
            for tag, attrs in self.parser.elements
            if "id" in attrs
        }
        self.assertEqual(by_id["main"][0], "main")
        self.assertEqual(by_id["main"][1].get("tabindex"), "-1")
        self.assertEqual(by_id["announcer"][1].get("aria-live"), "polite")
        self.assertEqual(by_id["toast-stack"][1].get("aria-live"), "polite")
        for dialog_id in ("settings-dialog", "handoff-dialog", "nickname-dialog"):
            attrs = by_id[dialog_id][1]
            self.assertEqual(attrs.get("role"), "dialog")
            self.assertEqual(attrs.get("aria-modal"), "true")
            self.assertIn(attrs.get("aria-labelledby"), by_id)
        self.assertIn('class="skip-link" href="#main"', self.html)

    def test_all_form_controls_have_an_accessible_name(self) -> None:
        for tag, attrs in self.parser.elements:
            if tag not in {"input", "select"}:
                continue
            self.assertTrue(
                attrs.get("aria-label") or attrs.get("aria-labelledby"),
                f"unnamed {tag} control {attrs.get('id', '')}",
            )

    def test_critical_end_to_end_flows_are_visible_and_inline(self) -> None:
        expected = (
            "Unofficial · Home Assistant",
            "Weber Connect for Home Assistant",
            "Unofficial · Read-only",
            "Set Up My Hub",
            "Connect once. Cook anywhere.",
            "Local only",
            "Use Weber app",
            "Weber app + Home Assistant",
            "Set up Weber app access",
            "Start pairing",
            "Release Bluetooth",
            "Reconnect now",
            "Manual reconnect",
            "Automatic reconnect is off",
            "Weber app access",
            "Bluetooth available",
            "Before pairing, fully close the Weber app on every nearby phone or tablet",
            "It cannot pair while the app is connected over Bluetooth",
            "The physical probe number will always remain visible",
            'data-action="edit-probe"',
            'act("pair", { phone_coexistence: true })',
            'act("cloud", { action: "pair" })',
            'act("handoff", { minutes: handoffSelection })',
            'act("resume")',
        )
        for value in expected:
            self.assertIn(value, self.html)
        self.assertNotIn("window.confirm", self.html)
        self.assertNotIn("window.prompt", self.html)
        self.assertNotIn("Until I return", self.html)
        self.assertNotIn("Phone session", self.html)
        self.assertNotIn("Use with phone", self.html)
        self.assertNotIn('id="header-status"', self.html)
        self.assertNotIn("status-pill", self.html)

    def test_panel_is_self_contained_and_responsive(self) -> None:
        self.assertIn('<link rel="icon" href="icon.png" type="image/png">', self.html)
        for tag, attrs in self.parser.elements:
            if tag in {"script", "img", "link"}:
                source = attrs.get("src") or attrs.get("href") or ""
                self.assertFalse(source.startswith(("http://", "https://")))
        for value in (
            "prefers-color-scheme: dark",
            "prefers-reduced-motion: reduce",
            "env(safe-area-inset-top)",
            "@media (max-width: 480px)",
            "@media (min-width: 721px)",
        ):
            self.assertIn(value, self.html)

    def test_embedded_javascript_parses(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        self.assertEqual(len(self.parser.scripts), 1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8") as handle:
            handle.write(self.parser.scripts[0])
            handle.flush()
            result = subprocess.run(
                [node, "--check", handle.name],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_panel as panel  # noqa: E402

COMPANION_ID = "00112233445566778899aabbccddeeff"
ADDRESS = "AA:BB:CC:DD:EE:FF"


def make_controller(data_dir: Path) -> panel.HubController:
    return panel.HubController(data_dir=data_dir, mqtt=None)


def make_summary(companion_id: str, address: str) -> dict:
    return {
        "paired_at": "2026-01-01T00:00:00+00:00",
        "companion_id": companion_id,
        "companion_records": [{"companion_id": companion_id}],
        "hub": {
            "display_name": "Weber Connect Hub",
            "model": "Connect Hub",
            "ble_address": address,
        },
    }


def snapshot(controller: panel.HubController) -> dict:
    return asyncio.run(controller.snapshot())


class PanelContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_fresh_install_starts_in_setup(self) -> None:
        controller = make_controller(self.data_dir)
        snap = snapshot(controller)
        self.assertEqual(snap["state"], "setup")
        self.assertFalse(snap["paired"])
        self.assertEqual(snap["settings"]["poll_seconds"], 10)

    def test_settings_persist_across_restarts(self) -> None:
        controller = make_controller(self.data_dir)
        asyncio.run(
            controller.update_settings(
                {
                    "poll_seconds": 60,
                    "handoff_minutes": 5,
                    "probe_names": {"1": "Brisket"},
                }
            )
        )

        reloaded = make_controller(self.data_dir)
        self.assertEqual(reloaded.settings.poll_seconds, 60)
        self.assertEqual(reloaded.settings.handoff_minutes, 5)
        self.assertEqual(reloaded.settings.probe_names, {1: "Brisket"})
        self.assertEqual(snapshot(reloaded)["settings"]["probe_names"], {"1": "Brisket"})

    def test_probe_nickname_keeps_number_in_live_snapshot(self) -> None:
        controller = make_controller(self.data_dir)
        controller.summary = make_summary(COMPANION_ID, ADDRESS)
        controller.settings = controller.settings.with_address(ADDRESS)
        controller.runtime.last_source = "ble"
        controller.runtime.last_good_state = {
            "connected": True,
            "source": "ble",
            "status": {
                "probe_count": 1,
                "probes": [{"probe_number": 1, "probe_temp_f": 205}],
            },
        }

        result = asyncio.run(controller.update_settings({"probe_names": {"1": "Brisket"}}))

        self.assertTrue(result["ok"])
        probe = snapshot(controller)["probes"][0]
        self.assertEqual(probe["probe_number"], 1)
        self.assertEqual(probe["nickname"], "Brisket")

    def test_settings_are_clamped(self) -> None:
        controller = make_controller(self.data_dir)
        asyncio.run(controller.update_settings({"poll_seconds": 1, "handoff_minutes": 9999}))
        self.assertEqual(controller.settings.poll_seconds, 10)
        self.assertEqual(controller.settings.handoff_minutes, 240)

    def test_invalid_settings_are_rejected_without_partial_update(self) -> None:
        controller = make_controller(self.data_dir)
        result = asyncio.run(
            controller.update_settings({"poll_seconds": 60, "handoff_minutes": "later"})
        )

        self.assertFalse(result["ok"])
        self.assertEqual(controller.settings.poll_seconds, 10)
        self.assertEqual(controller.settings.handoff_minutes, 15)

    def test_snapshot_exposes_stable_probe_capacity(self) -> None:
        controller = make_controller(self.data_dir)
        self.assertEqual(snapshot(controller)["max_probes"], panel.MAX_PROBES)

    def test_handoff_releases_and_resume_reconnects(self) -> None:
        controller = make_controller(self.data_dir)
        controller.summary = make_summary(COMPANION_ID, ADDRESS)
        controller.settings = controller.settings.with_address(ADDRESS)

        async def run() -> None:
            result = await controller.handoff(0)
            assert result["ok"]

        asyncio.run(run())
        snap = snapshot(controller)
        self.assertEqual(snap["state"], "handoff")
        self.assertFalse(snap["handoff"]["auto_resume"])

        asyncio.run(controller.resume())
        self.assertEqual(snapshot(controller)["state"], "connecting")

    def test_forget_returns_to_setup_and_keeps_keys(self) -> None:
        keys_file = self.data_dir / "pairing_keys.json"
        keys_file.write_text(json.dumps({"companion_id": COMPANION_ID}), encoding="utf-8")
        controller = make_controller(self.data_dir)
        controller.summary = make_summary(COMPANION_ID, ADDRESS)
        controller.settings = controller.settings.with_address(ADDRESS)

        asyncio.run(controller.forget())
        self.assertEqual(snapshot(controller)["state"], "setup")
        self.assertFalse((self.data_dir / "pairing_summary.json").exists())
        self.assertTrue(keys_file.exists())

    def test_handoff_requires_pairing(self) -> None:
        controller = make_controller(self.data_dir)
        result = asyncio.run(controller.handoff(5))
        self.assertFalse(result["ok"])

    def test_handoff_rejects_invalid_duration(self) -> None:
        controller = make_controller(self.data_dir)
        controller.summary = make_summary(COMPANION_ID, ADDRESS)
        controller.settings = controller.settings.with_address(ADDRESS)

        result = asyncio.run(controller.handoff("later"))

        self.assertFalse(result["ok"])
        self.assertEqual(snapshot(controller)["state"], "connecting")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_ble_scan as scanner  # noqa: E402
import weber_status_bridge as bridge  # noqa: E402


class ScannerContractTests(unittest.TestCase):
    def test_candidate_detection_uses_company_id_or_name(self) -> None:
        self.assertTrue(scanner.is_weber(None, {0x0DF2: b"x"}))
        self.assertTrue(scanner.is_weber("Weber Connect", {}))
        self.assertTrue(scanner.is_weber("June Oven", {}))
        self.assertFalse(scanner.is_weber(None, {}))
        self.assertFalse(scanner.is_weber("Headphones", {1: b"x"}))

    def test_make_record_normalizes_advertisement(self) -> None:
        device = SimpleNamespace(address="AA:BB", name="Fallback", rssi=-80)
        advertisement = SimpleNamespace(
            local_name="Weber Hub",
            rssi=-55,
            manufacturer_data={0x0DF2: b"\x01\x02"},
            service_uuids=["b", "a"],
            service_data={"service": b"\x03"},
        )

        record = scanner.make_record(device, advertisement)

        self.assertTrue(record["is_weber_candidate"])
        self.assertEqual(record["rssi"], -55)
        self.assertEqual(record["service_uuids"], ["a", "b"])
        self.assertEqual(record["manufacturer_data"]["0x0df2"]["hex"], "01:02")
        self.assertEqual(record["service_data"]["service"], "03")

    def test_scan_stops_when_weber_candidate_arrives(self) -> None:
        class FakeScanner:
            def __init__(self, callback):
                self.callback = callback
                self.stopped = False

            async def start(self):
                self.callback(
                    SimpleNamespace(address="AA:BB", name="Hub", rssi=-50),
                    SimpleNamespace(
                        local_name="Weber Connect",
                        rssi=-50,
                        manufacturer_data={},
                        service_uuids=[],
                        service_data={},
                    ),
                )

            async def stop(self):
                self.stopped = True

        with mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakScanner=FakeScanner)}):
            result = asyncio.run(scanner.scan(5, include_all=False, stop_on_weber=True))

        self.assertEqual(len(result["weber_candidates"]), 1)
        self.assertEqual(result["weber_candidates"][0]["address"], "AA:BB")


class BridgeSupportTests(unittest.TestCase):
    def args(self, **updates):
        values = {
            "address": "AA:BB:CC:DD:EE:FF",
            "pairing_summary": None,
            "companion_id": "00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff",
            "hub_name": " Kitchen Hub ",
            "hub_serial": " SERIAL ",
            "hub_model": " Connect Hub ",
            "hub_software_revision": " 1.2.3 ",
            "hub_wifi_mac": None,
            "mqtt_credentials_file": None,
            "mqtt_username": None,
            "mqtt_password": None,
        }
        values.update(updates)
        return SimpleNamespace(**values)

    def test_build_summary_normalizes_user_input(self) -> None:
        summary = bridge.build_summary_from_args(self.args())
        self.assertEqual(summary["companion_id"], "00112233445566778899aabbccddeeff")
        self.assertEqual(summary["hub"]["display_name"], "Kitchen Hub")
        self.assertEqual(summary["hub"]["serial_number"], "SERIAL")
        self.assertEqual(bridge.default_address(summary), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(bridge.device_id_from(summary, ""), "weber_connect_serial")

    def test_pairing_summary_rejects_missing_companion_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.json"
            path.write_text(json.dumps({"hub": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                bridge.load_pairing_summary(path)

            path.write_text(
                json.dumps({"companion_records": [{"companion_id": "abc"}], "hub": {}}),
                encoding="utf-8",
            )
            loaded = bridge.load_pairing_summary(path)
            self.assertEqual(loaded["companion_id"], "abc")

    def test_mqtt_credentials_load_from_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mqtt.json"
            path.write_text(json.dumps({"username": "user", "password": "secret"}), encoding="utf-8")
            args = self.args(mqtt_credentials_file=path)
            bridge.load_mqtt_credentials(args)
            self.assertEqual((args.mqtt_username, args.mqtt_password), ("user", "secret"))

    def test_mqtt_credentials_reject_incomplete_pairs(self) -> None:
        with self.assertRaises(ValueError):
            bridge.load_mqtt_credentials(self.args(mqtt_password="secret"))

    def test_parse_status_event_preserves_transport_metadata(self) -> None:
        event = {
            "source": "status",
            "received_at": "now",
            "decoded": {
                "sequence": 7,
                "envelope": {
                    "body_plain_candidate": {
                        "message_version": 10,
                        "parsed_payload": {"kind": "cook_session_status", "probes": []},
                    }
                },
            },
        }
        parsed = bridge.parse_status_event(event)
        self.assertEqual(parsed["transport_sequence"], 7)
        self.assertEqual(parsed["message_version"], 10)
        self.assertEqual(parsed["source"], "status")


if __name__ == "__main__":
    unittest.main()

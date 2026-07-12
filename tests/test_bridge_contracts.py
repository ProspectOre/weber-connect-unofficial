from __future__ import annotations

import asyncio
import json
import random
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_ble_pair as pair  # noqa: E402
import weber_status_bridge as bridge  # noqa: E402
from saber_frames import build_command_frame, bytes_to_hex, decode_hex_frame  # noqa: E402


class BridgeContractTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "topic_prefix": "weber_connect",
            "discovery_prefix": "homeassistant",
            "poll_seconds": 30,
            "discovery": True,
            "retain": True,
            "max_probes": 2,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def summary(self):
        return {
            "companion_id": "00112233445566778899aabbccddeeff",
            "hub": {
                "display_name": "Weber Connect Hub",
                "serial_number": "TESTSERIAL",
                "model": "Connect Hub",
                "software_revision": "1.2.3",
                "wifi_mac": None,
                "ble_address": "AA:BB:CC:DD:EE:FF",
            },
        }

    def status(self):
        return {
            "kind": "cook_session_status",
            "probe_count": 1,
            "probes": [
                {
                    "probe_number": 1,
                    "probe_temp_f": 205.5,
                    "probe_temp_c": 96.4,
                    "state": "PROBED",
                    "battery_level": 88,
                    "probe_type": "MEAT",
                }
            ],
        }

    def test_companion_id_normalization(self):
        self.assertEqual(
            bridge.validate_companion_id("00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF"),
            "00112233445566778899aabbccddeeff",
        )
        with self.assertRaises(ValueError):
            bridge.validate_companion_id("not-a-companion-id")

    def test_build_state_preserves_probe_slots(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )

        self.assertTrue(state["connected"])
        self.assertEqual(state["probe_1_temperature_f"], 205.5)
        self.assertEqual(state["probe_1_state"], "PROBED")
        self.assertEqual(state["probe_1_battery"], 88)
        self.assertIsNone(state["probe_2_temperature_f"])
        self.assertEqual(state["probe_2_state"], "No probe")
        self.assertEqual(state["probe_count"], 1)

    def test_paused_state_marks_disconnected_and_preserves_probe_slots(self):
        state = bridge.build_state(
            self.summary(),
            {},
            address="AA:BB:CC:DD:EE:FF",
            connected=False,
            max_probes=2,
        )

        self.assertFalse(state["connected"])
        self.assertEqual(state["probe_count"], 0)
        self.assertEqual(state["probes"], [])
        self.assertIsNone(state["probe_1_temperature_f"])
        self.assertEqual(state["probe_1_state"], "No probe")
        self.assertIsNone(state["probe_2_temperature_f"])
        self.assertEqual(state["probe_2_state"], "No probe")

    def test_pause_summary_does_not_require_companion_id(self):
        args = SimpleNamespace(
            address="AA:BB:CC:DD:EE:FF",
            pairing_summary=Path("/tmp/definitely-missing-weber-pairing-summary.json"),
            companion_id=None,
            hub_name="Weber Connect Hub",
            hub_serial=None,
            hub_model="Connect Hub",
            hub_software_revision=None,
            hub_wifi_mac=None,
        )

        summary = bridge.load_bridge_summary(args, allow_unpaired=True)

        self.assertEqual(summary["companion_id"], "0" * 32)
        self.assertEqual(summary["companion_records"], [])
        self.assertEqual(summary["hub"]["ble_address"], "AA:BB:CC:DD:EE:FF")

    def test_mqtt_discovery_contract(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )
        plan = bridge.build_mqtt_publish_plan(self.args(), state, self.summary())

        self.assertEqual(len(plan), 7)
        self.assertEqual(plan[-1]["topic"], "weber_connect/weber_connect_testserial/state")
        self.assertTrue(plan[-1]["retain"])
        self.assertEqual(json.loads(plan[-1]["payload"])["probe_1_temperature_f"], 205.5)

        config_topics = [row["topic"] for row in plan[:-1]]
        self.assertIn(
            "homeassistant/sensor/weber_connect_testserial_probe_1_temperature/config",
            config_topics,
        )
        self.assertTrue(all(row["retain"] for row in plan[:-1]))

        temp_payload = json.loads(plan[0]["payload"])
        self.assertEqual(temp_payload["name"], "Probe 1 Temperature")
        self.assertEqual(temp_payload["state_topic"], "weber_connect/weber_connect_testserial/state")
        self.assertEqual(temp_payload["unit_of_measurement"], "\u00b0F")
        self.assertEqual(temp_payload["device_class"], "temperature")
        self.assertEqual(temp_payload["state_class"], "measurement")
        self.assertEqual(temp_payload["expire_after"], 120)
        self.assertEqual(temp_payload["device"]["identifiers"], ["TESTSERIAL"])
        self.assertEqual(temp_payload["device"]["manufacturer"], "Weber")
        self.assertEqual(temp_payload["origin"]["sw"], bridge.VERSION)
        self.assertNotIn("availability_topic", temp_payload)

    def test_persistent_runtime_discovery_includes_availability(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )
        plan = bridge.build_mqtt_publish_plan(
            self.args(availability=True),
            state,
            self.summary(),
        )

        temperature = json.loads(plan[0]["payload"])
        self.assertEqual(
            temperature["availability_topic"],
            "weber_connect/weber_connect_testserial/availability",
        )

    def test_mqtt_plan_without_discovery_publishes_only_state(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )
        plan = bridge.build_mqtt_publish_plan(
            self.args(discovery=False, retain=False),
            state,
            self.summary(),
        )

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["topic"], "weber_connect/weber_connect_testserial/state")
        self.assertFalse(plan[0]["retain"])

    def test_legacy_topic_template_still_renders(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )
        plan = bridge.build_mqtt_publish_plan(
            self.args(topic_prefix="weber_connect/{device_id}"),
            state,
            self.summary(),
        )

        self.assertEqual(plan[-1]["topic"], "weber_connect/weber_connect_testserial/state")

    def test_malformed_topic_template_is_sanitized(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )
        plan = bridge.build_mqtt_publish_plan(
            self.args(topic_prefix="weber_connect/{device_id}/}"),
            state,
            self.summary(),
        )

        self.assertEqual(plan[-1]["topic"], "weber_connect/weber_connect_testserial/state")

    def test_custom_topic_root_appends_device_id(self):
        state = bridge.build_state(
            self.summary(),
            self.status(),
            address="AA:BB:CC:DD:EE:FF",
            connected=True,
            max_probes=2,
        )
        plan = bridge.build_mqtt_publish_plan(
            self.args(topic_prefix="outdoor/kitchen"),
            state,
            self.summary(),
        )

        self.assertEqual(plan[-1]["topic"], "outdoor/kitchen/weber_connect_testserial/state")

    def test_release_ble_connection_noop_without_bluetoothctl(self):
        with mock.patch.object(bridge.shutil, "which", return_value=None):
            self.assertFalse(bridge.release_ble_connection("AA:BB:CC:DD:EE:FF"))

    def test_release_ble_connection_disconnects_via_bluetoothctl(self):
        completed = SimpleNamespace(returncode=0, stdout="Successful disconnected\n", stderr="")
        with (
            mock.patch.object(bridge.shutil, "which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(bridge.subprocess, "run", return_value=completed) as run,
        ):
            self.assertTrue(bridge.release_ble_connection("AA:BB:CC:DD:EE:FF"))
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/bluetoothctl", "disconnect", "AA:BB:CC:DD:EE:FF"],
        )

    def test_release_ble_connection_tolerates_no_active_connection(self):
        completed = SimpleNamespace(returncode=1, stdout="", stderr="Device not connected\n")
        with (
            mock.patch.object(bridge.shutil, "which", return_value="/usr/bin/bluetoothctl"),
            mock.patch.object(bridge.subprocess, "run", return_value=completed),
        ):
            self.assertFalse(bridge.release_ble_connection("AA:BB:CC:DD:EE:FF"))

    def test_status_read_returns_as_soon_as_valid_status_arrives(self):
        status = self.status()

        class FakeBleakClient:
            def __init__(self, _address, timeout):
                self.timeout = timeout
                self.is_connected = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                self.is_connected = False

            async def start_notify(self, uuid, callback):
                if uuid == bridge.STATUS_UUID:
                    callback(uuid, b"\x00")

            async def stop_notify(self, _uuid):
                return None

            async def write_gatt_char(self, *_args, **_kwargs):
                return None

            async def read_gatt_char(self, _uuid):
                return b""

        started = time.monotonic()
        with (
            mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=FakeBleakClient)}),
            mock.patch.object(bridge, "parse_status_event", return_value=status),
        ):
            result = asyncio.run(
                bridge.read_status_once(
                    address="AA:BB:CC:DD:EE:FF",
                    companion_id="00112233445566778899aabbccddeeff",
                    version=10,
                    listen_seconds=5,
                    timeout=20,
                    write_without_response=False,
                )
            )

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(result["latest_status"], status)

    def test_frame_decoder_tolerates_arbitrary_bytes(self):
        generator = random.Random(20260711)
        for length in range(257):
            payload = bytes(generator.randrange(256) for _ in range(length))
            decoded = decode_hex_frame(bytes_to_hex(payload))
            self.assertIsInstance(decoded, dict)

    def test_pairing_frame_contract(self):
        companion_id = "00112233445566778899aabbccddeeff"
        companion_public_key = "aa" * 64
        frame = pair.build_pairing_frame(
            sequence=1,
            version=11,
            companion_id=companion_id,
            companion_public_key=companion_public_key,
            display_name="Home Assistant",
        )

        decoded = decode_hex_frame(bytes_to_hex(frame))
        envelope = decoded["envelope"]
        candidate = envelope["body_plain_candidate"]
        self.assertTrue(decoded["length_ok"])
        self.assertTrue(envelope["crc_ok"])
        self.assertEqual(candidate["message_version"], 11)
        self.assertEqual(candidate["type_name"], "OUTGOING_PAIRING_REQUEST")
        self.assertEqual(candidate["payload_length"], 95)
        self.assertTrue(candidate["payload_hex"].startswith("00:11:22:33"))

    def test_pairing_response_summary_contract(self):
        appliance_id = bytes(range(16))
        appliance_public_key = bytes(range(64))
        response_payload = appliance_id + appliance_public_key + bytes([0])
        response_frame = build_command_frame(7, 10, 0x85, response_payload)
        event = {
            "source": "response-read",
            "received_at": "2026-05-10T00:00:00+00:00",
            "decoded": decode_hex_frame(bytes_to_hex(response_frame)),
        }

        response = pair.extract_pairing_response(event)
        self.assertIsNotNone(response)
        self.assertEqual(response["status"], "CONFIRMED")
        self.assertEqual(response["transport_sequence"], 7)
        self.assertEqual(response["message_version"], 10)

        keys = {
            "display_name": "Home Assistant",
            "companion_id": "00112233445566778899aabbccddeeff",
            "companion_public_key": "aa" * 64,
        }
        summary = pair.build_pairing_summary(
            address="AA:BB:CC:DD:EE:FF",
            keys=keys,
            pairing_response=response,
            hub_name="Weber Connect Hub",
            hub_serial="TESTSERIAL",
            hub_model="Connect Hub",
            hub_software_revision="2.0.3_7398",
            hub_wifi_mac=None,
        )

        self.assertEqual(summary["companion_id"], keys["companion_id"])
        self.assertEqual(summary["companion_records"][0]["companion_public_key"], "aa" * 64)
        self.assertEqual(summary["hub"]["ble_address"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(summary["pairing_response"]["status"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()

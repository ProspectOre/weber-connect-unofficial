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

import weber_status_bridge as bridge  # noqa: E402
from saber_frames import build_command_frame  # noqa: E402

COMPANION_ID = "00112233445566778899aabbccddeeff"


class _BreakError(Exception):
    """Sentinel used to break out of an otherwise infinite bridge loop."""


def _Break():
    async def _sleep(*_args, **_kwargs):
        raise _BreakError()

    return _sleep


def status_frame() -> bytes:
    # type 0x80 (INCOMING_STATUS) with an empty TLV body still decodes to a
    # cook_session_status payload with zero probes.
    return build_command_frame(4, 10, 0x80, b"")


def summary_with_serial() -> dict:
    return {
        "companion_id": COMPANION_ID,
        "companion_records": [{"companion_id": COMPANION_ID}],
        "hub": {
            "display_name": "Weber Connect Hub",
            "serial_number": "TESTSERIAL",
            "model": "Connect Hub",
            "software_revision": "1.2.3",
            "wifi_mac": None,
            "ble_address": "AA:BB:CC:DD:EE:FF",
        },
    }


class PureBridgeHelperTests(unittest.TestCase):
    def test_device_id_from_falls_back_to_address_then_companion(self) -> None:
        no_serial = {"companion_id": COMPANION_ID, "hub": {}}
        self.assertEqual(
            bridge.device_id_from(no_serial, "AA:BB:CC:DD:EE:FF"),
            "weber_connect_aa_bb_cc_dd_ee_ff",
        )
        self.assertEqual(
            bridge.device_id_from(no_serial, ""),
            "weber_connect_ccddeeff",
        )

    def test_load_pairing_summary_requires_companion_id_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(
                json.dumps({"companion_records": [{}], "hub": {}}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                bridge.load_pairing_summary(path)

    def test_load_bridge_summary_paths(self) -> None:
        # allow_unpaired branch when nothing is provided.
        args = SimpleNamespace(
            pairing_summary=None,
            companion_id=None,
            address="AA:BB:CC:DD:EE:FF",
            hub_name=None,
            hub_serial=None,
            hub_model=None,
            hub_software_revision=None,
            hub_wifi_mac=None,
        )
        unpaired = bridge.load_bridge_summary(args, allow_unpaired=True)
        self.assertEqual(unpaired["companion_records"], [])
        # Without allow_unpaired it raises.
        with self.assertRaises(ValueError):
            bridge.load_bridge_summary(args, allow_unpaired=False)

    def test_load_bridge_summary_reads_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps(summary_with_serial()), encoding="utf-8")
            args = SimpleNamespace(pairing_summary=path, companion_id=None)
            loaded = bridge.load_bridge_summary(args)
            self.assertEqual(loaded["companion_id"], COMPANION_ID)

    def test_load_mqtt_credentials_incomplete_file_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mqtt.json"
            path.write_text(json.dumps({"username": "user"}), encoding="utf-8")
            args = SimpleNamespace(
                mqtt_credentials_file=path, mqtt_username=None, mqtt_password=None
            )
            with self.assertRaises(ValueError):
                bridge.load_mqtt_credentials(args)

            path.write_text(json.dumps({"password": "secret"}), encoding="utf-8")
            args = SimpleNamespace(
                mqtt_credentials_file=path, mqtt_username=None, mqtt_password=None
            )
            with self.assertRaises(ValueError):
                bridge.load_mqtt_credentials(args)

    def test_default_address_requires_value(self) -> None:
        with self.assertRaises(ValueError):
            bridge.default_address({"hub": {"ble_address": None}})

    def test_write_json_atomic_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            bridge.write_json_atomic(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text())["ok"], True)

    def test_parse_status_event_ignores_non_status(self) -> None:
        event = {
            "decoded": {
                "envelope": {
                    "body_plain_candidate": {"parsed_payload": {"kind": "error"}}
                }
            }
        }
        self.assertIsNone(bridge.parse_status_event(event))

    def test_build_state_skips_probe_without_number(self) -> None:
        status = {
            "probes": [{"probe_temp_f": 200.0}],  # no probe_number
            "probe_count": 1,
        }
        state = bridge.build_state(
            summary_with_serial(), status, "AA:BB:CC:DD:EE:FF", True, 2
        )
        self.assertIsNone(state["probe_1_temperature_f"])
        self.assertEqual(state["probe_1_state"], "No probe")

    def test_render_topic_prefix_branches(self) -> None:
        # Known-key substitution.
        self.assertEqual(
            bridge.render_topic_prefix(
                "root/{serial}", device_id="dev", object_slug="obj", serial="ser"
            ),
            "root/ser",
        )
        # Stray braces with no known key get stripped, then device id appended.
        self.assertEqual(
            bridge.render_topic_prefix(
                "root/{unknown}", device_id="dev", object_slug="obj", serial="ser"
            ),
            "root/unknown/dev",
        )
        # Empty template falls back to the default root plus device id.
        self.assertEqual(
            bridge.render_topic_prefix(
                "/", device_id="dev", object_slug="obj", serial="ser"
            ),
            "weber_connect/dev",
        )
        # Last path segment already equal to device id short-circuits.
        self.assertEqual(
            bridge.render_topic_prefix(
                "root/dev", device_id="dev", object_slug="obj", serial="ser"
            ),
            "root/dev",
        )


def fake_mqtt_module(publish_result=None):
    published = []

    class Result:
        def __init__(self):
            self.rc = 0

        def wait_for_publish(self, timeout=None):
            return None

        def is_published(self):
            return True

    class Client:
        def __init__(self, *args, **kwargs):
            self.args = args

        def username_pw_set(self, username, password):
            self.credentials = (username, password)

        def connect(self, host, port, keepalive=0):
            return 0

        def loop_start(self):
            return None

        def publish(self, topic, payload, qos=0, retain=False):
            published.append(topic)
            return publish_result or Result()

        def loop_stop(self):
            return None

        def disconnect(self):
            return None

    client_module = SimpleNamespace(
        Client=Client,
        MQTT_ERR_SUCCESS=0,
        error_string=lambda rc: f"err{rc}",
    )
    enums_module = SimpleNamespace(CallbackAPIVersion=SimpleNamespace(VERSION2=2))
    mqtt_pkg = SimpleNamespace(client=client_module, enums=enums_module)
    paho_pkg = SimpleNamespace(mqtt=mqtt_pkg)
    modules = {
        "paho": paho_pkg,
        "paho.mqtt": mqtt_pkg,
        "paho.mqtt.client": client_module,
        "paho.mqtt.enums": enums_module,
    }
    return modules, published


class MqttPublishTests(unittest.TestCase):
    def args(self, **overrides) -> SimpleNamespace:
        values = {
            "topic_prefix": "weber_connect",
            "discovery_prefix": "homeassistant",
            "poll_seconds": 30,
            "discovery": True,
            "retain": True,
            "max_probes": 2,
            "mqtt_host": "127.0.0.1",
            "mqtt_port": 1883,
            "mqtt_username": "user",
            "mqtt_password": "secret",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_mqtt_publish_sends_full_plan(self) -> None:
        summary = summary_with_serial()
        state = bridge.build_state(summary, {}, "AA:BB:CC:DD:EE:FF", True, 2)
        modules, published = fake_mqtt_module()
        with mock.patch.dict(sys.modules, modules):
            bridge.mqtt_publish(self.args(), state, summary)
        self.assertTrue(any(t.endswith("/state") for t in published))

    def test_mqtt_publish_raises_when_not_published(self) -> None:
        summary = summary_with_serial()
        state = bridge.build_state(summary, {}, "AA:BB:CC:DD:EE:FF", True, 2)

        class FailingResult:
            rc = 0

            def wait_for_publish(self, timeout=None):
                return None

            def is_published(self):
                return False

        modules, _ = fake_mqtt_module(publish_result=FailingResult())
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(TimeoutError):
                bridge.mqtt_publish(self.args(discovery=False), state, summary)

    def test_mqtt_publish_import_error(self) -> None:
        summary = summary_with_serial()
        state = bridge.build_state(summary, {}, "AA:BB:CC:DD:EE:FF", True, 2)
        with mock.patch.dict(sys.modules, {"paho.mqtt.client": None}):
            with self.assertRaises(RuntimeError):
                bridge.mqtt_publish(self.args(), state, summary)

    def test_mqtt_publish_without_username_and_connect_failure(self) -> None:
        summary = summary_with_serial()
        state = bridge.build_state(summary, {}, "AA:BB:CC:DD:EE:FF", True, 2)
        modules, _ = fake_mqtt_module()
        # Connect returns a non-success code -> RuntimeError.
        modules["paho.mqtt.client"].Client.connect = (
            lambda self, host, port, keepalive=0: 1
        )
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(RuntimeError):
                bridge.mqtt_publish(
                    self.args(discovery=False, mqtt_username=None), state, summary
                )

    def test_mqtt_publish_rejects_nonzero_publish_rc(self) -> None:
        summary = summary_with_serial()
        state = bridge.build_state(summary, {}, "AA:BB:CC:DD:EE:FF", True, 2)

        class BadRcResult:
            rc = 5

            def wait_for_publish(self, timeout=None):
                return None

            def is_published(self):
                return True

        modules, _ = fake_mqtt_module(publish_result=BadRcResult())
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(RuntimeError):
                bridge.mqtt_publish(self.args(discovery=False), state, summary)


class ReadStatusOnceTests(unittest.TestCase):
    def make_client(
        self, *, notify_raises=False, session_read_raises=False, stop_raises=False
    ):
        class FakeClient:
            def __init__(self, address, timeout):
                self.is_connected = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                self.is_connected = False
                return False

            async def start_notify(self, uuid, callback):
                if notify_raises:
                    raise RuntimeError("cannot subscribe")

            async def stop_notify(self, _uuid):
                if stop_raises:
                    raise RuntimeError("stop failed")
                return None

            async def write_gatt_char(self, uuid, data, response=True):
                return None

            async def read_gatt_char(self, uuid):
                if session_read_raises:
                    raise RuntimeError("cannot read session")
                return b"\x01"

        return FakeClient

    def run_read(self, client, **overrides):
        params = {
            "address": "AA:BB:CC:DD:EE:FF",
            "companion_id": COMPANION_ID,
            "version": 10,
            "listen_seconds": 0.01,
            "timeout": 20,
            "write_without_response": False,
        }
        params.update(overrides)
        with mock.patch.dict(
            sys.modules, {"bleak": SimpleNamespace(BleakClient=client)}
        ):
            return asyncio.run(bridge.read_status_once(**params))

    def test_timeout_without_status(self) -> None:
        result = self.run_read(self.make_client())
        self.assertIsNone(result["latest_status"])

    def test_subscribe_and_session_read_failures_are_tolerated(self) -> None:
        result = self.run_read(
            self.make_client(notify_raises=True, session_read_raises=True)
        )
        self.assertIsNone(result["latest_status"])

    def test_stop_notify_failure_is_swallowed(self) -> None:
        result = self.run_read(self.make_client(stop_raises=True))
        self.assertIsNone(result["latest_status"])

    def test_import_error_when_bleak_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    bridge.read_status_once(
                        address="AA:BB:CC:DD:EE:FF",
                        companion_id=COMPANION_ID,
                        version=10,
                        listen_seconds=0.01,
                        timeout=1,
                        write_without_response=False,
                    )
                )


class ReleaseBleTests(unittest.TestCase):
    def test_release_ble_connection_handles_subprocess_error(self) -> None:
        with (
            mock.patch.object(
                bridge.shutil, "which", return_value="/usr/bin/bluetoothctl"
            ),
            mock.patch.object(
                bridge.subprocess, "run", side_effect=OSError("boom")
            ),
        ):
            self.assertFalse(bridge.release_ble_connection("AA:BB:CC:DD:EE:FF"))


class RunBridgeTests(unittest.TestCase):
    def args(self, tmp: Path, **overrides) -> SimpleNamespace:
        values = {
            "address": "AA:BB:CC:DD:EE:FF",
            "pairing_summary": None,
            "companion_id": COMPANION_ID,
            "hub_name": "Weber Connect Hub",
            "hub_serial": "TESTSERIAL",
            "hub_model": "Connect Hub",
            "hub_software_revision": "1.2.3",
            "hub_wifi_mac": None,
            "json_out": tmp / "out.json",
            "version": 10,
            "listen_seconds": 0.01,
            "timeout": 1,
            "write_without_response": False,
            "pause_ble": False,
            "continuous": False,
            "poll_seconds": 0.01,
            "mqtt_host": None,
            "mqtt_port": 1883,
            "mqtt_username": None,
            "mqtt_password": None,
            "mqtt_credentials_file": None,
            "topic_prefix": "weber_connect",
            "discovery_prefix": "homeassistant",
            "discovery": True,
            "retain": True,
            "max_probes": 2,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_pause_ble_writes_disconnected_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), pause_ble=True)
            with mock.patch.object(bridge, "release_ble_connection", return_value=True):
                rc = asyncio.run(bridge.run_bridge(args))
            self.assertEqual(rc, 0)
            self.assertFalse(json.loads(args.json_out.read_text())["connected"])

    def test_pause_ble_mqtt_failure_returns_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), pause_ble=True, mqtt_host="127.0.0.1")
            with (
                mock.patch.object(bridge, "release_ble_connection", return_value=True),
                mock.patch.object(
                    bridge, "mqtt_publish", side_effect=RuntimeError("no broker")
                ),
            ):
                rc = asyncio.run(bridge.run_bridge(args))
            self.assertEqual(rc, 3)

    def test_status_success_writes_state_and_publishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), mqtt_host="127.0.0.1")
            status = {"kind": "cook_session_status", "probes": [], "probe_count": 0}

            async def fake_read(**_kwargs):
                return {"latest_status": status, "connected": True}

            published = []
            with (
                mock.patch.object(bridge, "read_status_once", side_effect=fake_read),
                mock.patch.object(
                    bridge, "mqtt_publish", side_effect=lambda *a: published.append(a)
                ),
            ):
                rc = asyncio.run(bridge.run_bridge(args))
            self.assertEqual(rc, 0)
            self.assertTrue(published)

    def test_status_read_exception_returns_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))

            async def fake_read(**_kwargs):
                raise RuntimeError("ble down")

            with mock.patch.object(bridge, "read_status_once", side_effect=fake_read):
                rc = asyncio.run(bridge.run_bridge(args))
            self.assertEqual(rc, 1)

    def test_continuous_pause_ble_sleeps_after_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(
                Path(tmp), pause_ble=True, continuous=True, mqtt_host="127.0.0.1"
            )
            with (
                mock.patch.object(bridge, "release_ble_connection", return_value=True),
                mock.patch.object(bridge, "mqtt_publish", return_value=None),
                mock.patch.object(
                    bridge.asyncio, "sleep", side_effect=_Break()
                ),
            ):
                with self.assertRaises(_BreakError):
                    asyncio.run(bridge.run_bridge(args))

    def test_continuous_read_failure_sleeps_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), continuous=True)

            async def fake_read(**_kwargs):
                raise RuntimeError("ble down")

            with (
                mock.patch.object(bridge, "read_status_once", side_effect=fake_read),
                mock.patch.object(bridge.asyncio, "sleep", side_effect=_Break()),
            ):
                with self.assertRaises(_BreakError):
                    asyncio.run(bridge.run_bridge(args))

    def test_continuous_status_success_and_mqtt_failure_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp), continuous=True, mqtt_host="127.0.0.1")
            status = {"kind": "cook_session_status", "probes": [], "probe_count": 0}

            async def fake_read(**_kwargs):
                return {"latest_status": status, "connected": True}

            with (
                mock.patch.object(bridge, "read_status_once", side_effect=fake_read),
                mock.patch.object(
                    bridge, "mqtt_publish", side_effect=RuntimeError("no broker")
                ),
                mock.patch.object(bridge.asyncio, "sleep", side_effect=_Break()),
            ):
                with self.assertRaises(_BreakError):
                    asyncio.run(bridge.run_bridge(args))

    def test_no_status_writes_raw_and_returns_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(Path(tmp))

            async def fake_read(**_kwargs):
                return {"latest_status": None, "events": []}

            with mock.patch.object(bridge, "read_status_once", side_effect=fake_read):
                rc = asyncio.run(bridge.run_bridge(args))
            self.assertEqual(rc, 2)


class RunBridgeUntilStoppedTests(unittest.TestCase):
    def args(self) -> SimpleNamespace:
        return SimpleNamespace(
            address="AA:BB:CC:DD:EE:FF",
            pairing_summary=None,
            companion_id=COMPANION_ID,
            hub_name="Weber Connect Hub",
            hub_serial="TESTSERIAL",
            hub_model="Connect Hub",
            hub_software_revision="1.2.3",
            hub_wifi_mac=None,
        )

    def test_runs_and_releases_on_shutdown(self) -> None:
        async def fake_run(_args):
            return 0

        with (
            mock.patch.object(bridge, "run_bridge", side_effect=fake_run),
            mock.patch.object(bridge, "release_ble_connection", return_value=True) as rel,
        ):
            rc = asyncio.run(bridge.run_bridge_until_stopped(self.args()))
        self.assertEqual(rc, 0)
        self.assertTrue(rel.called)

    def test_main_parses_args_and_runs(self) -> None:
        argv = [
            "weber_status_bridge",
            "--address",
            "AA:BB:CC:DD:EE:FF",
            "--companion-id",
            COMPANION_ID,
            "--log-level",
            "warning",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(bridge, "run_bridge_until_stopped", new=mock.Mock(return_value=None)),
            mock.patch.object(bridge.asyncio, "run", return_value=0) as run,
        ):
            rc = bridge.main()
        self.assertEqual(rc, 0)
        self.assertTrue(run.called)


if __name__ == "__main__":
    unittest.main()

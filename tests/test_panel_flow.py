from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_panel as panel  # noqa: E402

COMPANION_ID = "00112233445566778899aabbccddeeff"
ADDRESS = "AA:BB:CC:DD:EE:FF"


def summary() -> dict:
    return {
        "companion_id": COMPANION_ID,
        "companion_records": [{"companion_id": COMPANION_ID}],
        "hub": {
            "display_name": "Weber Connect Hub",
            "model": "Connect Hub",
            "ble_address": ADDRESS,
        },
    }


class LoadMqttTests(unittest.TestCase):
    def test_no_host_returns_none(self) -> None:
        args = argparse.Namespace(mqtt_host=None)
        self.assertIsNone(panel.load_mqtt(args))

    def test_host_without_credentials(self) -> None:
        args = argparse.Namespace(
            mqtt_host="mqtt.local",
            mqtt_port=1883,
            mqtt_credentials_file=None,
        )
        self.assertEqual(panel.load_mqtt(args), {"host": "mqtt.local", "port": 1883})

    def test_host_with_valid_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / "creds.json"
            creds.write_text(
                json.dumps({"username": "u", "password": "p"}), encoding="utf-8"
            )
            args = argparse.Namespace(
                mqtt_host="mqtt.local",
                mqtt_port=1883,
                mqtt_credentials_file=creds,
            )
            result = panel.load_mqtt(args)
            self.assertEqual(result["username"], "u")
            self.assertEqual(result["password"], "p")

    def test_host_with_invalid_credentials_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / "creds.json"
            creds.write_text("[]", encoding="utf-8")  # non-object → ValueError
            args = argparse.Namespace(
                mqtt_host="mqtt.local",
                mqtt_port=1883,
                mqtt_credentials_file=creds,
            )
            with self.assertLogs("weber_connect_panel", level="WARNING"):
                result = panel.load_mqtt(args)
            self.assertEqual(result, {"host": "mqtt.local", "port": 1883})


class PanelFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def controller(self, dependencies=None, *, mqtt=None) -> panel.HubController:
        return panel.HubController(
            self.data_dir, mqtt=mqtt, dependencies=dependencies
        )

    def paired(self, dependencies=None, *, mqtt=None) -> panel.HubController:
        controller = self.controller(dependencies, mqtt=mqtt)
        controller.summary = summary()
        controller.settings = controller.settings.with_address(ADDRESS)
        return controller

    # -- lifecycle ----------------------------------------------------------

    async def test_start_is_idempotent(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        await controller.start()
        await controller.start()  # second start returns immediately
        await controller.stop()

    async def test_start_while_closing_raises(self) -> None:
        controller = self.controller()
        controller._closing = True
        with self.assertRaises(RuntimeError):
            await controller.start()

    async def test_stop_is_idempotent(self) -> None:
        controller = self.controller()
        await controller.stop()
        await controller.stop()

    async def test_start_reschedules_indefinite_handoff(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        controller.runtime.handoff_active = True
        controller.runtime.handoff_until = controller.dependencies.wall_time() + 3600
        await controller.start()
        self.assertIsNotNone(controller._auto_resume_task)
        await controller.stop()

    # -- connection_state ---------------------------------------------------

    async def test_state_reports_pairing_and_scanning(self) -> None:
        controller = self.controller()
        controller.runtime.pairing = True
        self.assertEqual(controller.state(), "pairing")
        controller.runtime.pairing = False
        controller.runtime.scanning = True
        self.assertEqual(controller.state(), "scanning")

    # -- scan ---------------------------------------------------------------

    async def test_start_scan_while_closing(self) -> None:
        controller = self.controller()
        controller._closing = True
        result = await controller.start_scan()
        self.assertFalse(result["ok"])

    async def test_scan_task_releases_existing_connection(self) -> None:
        released: list[str] = []

        async def scan(*_a, **_k):
            return {"weber_candidates": [{"address": ADDRESS, "local_name": "Hub"}]}

        controller = self.paired(
            panel.ControllerDependencies(
                scan=scan,
                release=lambda address: released.append(address) is None,
            )
        )
        await controller.start_scan()
        while controller.runtime.scanning:
            await asyncio.sleep(0)
        self.assertEqual(released, [ADDRESS])
        self.assertEqual(controller.runtime.candidates[0]["address"], ADDRESS)
        await controller.stop()

    async def test_scan_no_candidates_sets_setup_error(self) -> None:
        async def scan(*_a, **_k):
            return {"weber_candidates": []}

        controller = self.controller(panel.ControllerDependencies(scan=scan))
        await controller.start_scan()
        while controller.runtime.scanning:
            await asyncio.sleep(0)
        self.assertIn("No hub found", controller.runtime.setup_error)
        await controller.stop()

    async def test_scan_failure_sets_setup_error(self) -> None:
        async def scan(*_a, **_k):
            raise RuntimeError("radio down")

        controller = self.controller(panel.ControllerDependencies(scan=scan))
        with self.assertLogs("weber_connect_panel", level="ERROR"):
            await controller.start_scan()
            while controller.runtime.scanning:
                await asyncio.sleep(0)
        self.assertIn("Bluetooth scan failed", controller.runtime.setup_error)
        await controller.stop()

    # -- pair ---------------------------------------------------------------

    async def test_pair_while_closing(self) -> None:
        controller = self.controller()
        controller._closing = True
        result = await controller.pair(ADDRESS)
        self.assertFalse(result["ok"])

    async def test_pair_rejects_non_string_address(self) -> None:
        controller = self.controller()
        result = await controller.pair(1234)
        self.assertFalse(result["ok"])

    async def test_pair_rejects_non_boolean_phone_coexistence(self) -> None:
        controller = self.controller()
        result = await controller.pair(ADDRESS, phone_coexistence="yes")
        self.assertFalse(result["ok"])
        self.assertIn("boolean", result["error"])

    async def test_pair_rejects_concurrent_operation(self) -> None:
        controller = self.controller()
        controller.runtime.scanning = True
        result = await controller.pair(ADDRESS)
        self.assertFalse(result["ok"])

    async def test_pair_without_address_scans_and_finds_none(self) -> None:
        async def scan(*_a, **_k):
            return {"weber_candidates": []}

        controller = self.controller(panel.ControllerDependencies(scan=scan))
        with self.assertLogs("weber_connect_panel", level="ERROR"):
            await controller.pair(None)
            while controller.runtime.pairing:
                await asyncio.sleep(0)
        self.assertIn("No hub found nearby", controller.runtime.setup_error)
        await controller.stop()

    async def test_pair_candidate_missing_address(self) -> None:
        async def scan(*_a, **_k):
            return {"weber_candidates": [{"address": None}]}

        controller = self.controller(panel.ControllerDependencies(scan=scan))
        with self.assertLogs("weber_connect_panel", level="ERROR"):
            await controller.pair(None)
            while controller.runtime.pairing:
                await asyncio.sleep(0)
        self.assertIn("no usable Bluetooth address", controller.runtime.setup_error)
        await controller.stop()

    async def test_pair_no_response_logs_events(self) -> None:
        async def pair(_args, _keys):
            return {
                "events": [
                    {
                        "source": "hub",
                        "length": 12,
                        "decoded": {
                            "envelope": {
                                "body_plain_candidate": {"type_name": "STATUS"}
                            }
                        },
                    },
                    {},
                ]
            }

        controller = self.controller(
            panel.ControllerDependencies(
                pair=pair,
                key_loader=lambda **_k: {"companion_id": COMPANION_ID},
            )
        )
        with self.assertLogs("weber_connect_panel", level="DEBUG"):
            await controller.pair(ADDRESS)
            while controller.runtime.pairing:
                await asyncio.sleep(0)
        self.assertIn("did not confirm", controller.runtime.setup_error)
        await controller.stop()

    async def test_pair_no_events_logs_debug(self) -> None:
        async def pair(_args, _keys):
            return {"events": []}

        controller = self.controller(
            panel.ControllerDependencies(
                pair=pair,
                key_loader=lambda **_k: {"companion_id": COMPANION_ID},
            )
        )
        await controller.pair(ADDRESS)
        while controller.runtime.pairing:
            await asyncio.sleep(0)
        self.assertIn("did not confirm", controller.runtime.setup_error)
        await controller.stop()

    async def test_pair_declined_status(self) -> None:
        async def pair(_args, _keys):
            return {"pairing_response": {"status": "REJECTED"}}

        controller = self.controller(
            panel.ControllerDependencies(
                pair=pair,
                key_loader=lambda **_k: {"companion_id": COMPANION_ID},
            )
        )
        with self.assertLogs("weber_connect_panel", level="ERROR"):
            await controller.pair(ADDRESS)
            while controller.runtime.pairing:
                await asyncio.sleep(0)
        self.assertIn("declined pairing", controller.runtime.setup_error)
        await controller.stop()

    # -- handoff ------------------------------------------------------------

    async def test_timed_handoff_schedules_auto_resume(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        result = await controller.handoff(5)
        self.assertTrue(result["ok"])
        self.assertIsNotNone(controller._auto_resume_task)
        snap = await controller.snapshot()
        self.assertTrue(snap["handoff"]["auto_resume"])
        self.assertGreater(snap["handoff"]["remaining_seconds"], 0)
        await controller.stop()

    async def test_handoff_release_failure_reverts(self) -> None:
        def release(_address):
            raise RuntimeError("stuck")

        controller = self.paired(
            panel.ControllerDependencies(release=release)
        )
        result = await controller.handoff(5)
        self.assertFalse(result["ok"])
        self.assertFalse(controller.runtime.handoff_active)
        await controller.stop()

    async def test_auto_resume_reconnects_after_window(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        controller.runtime.handoff_active = True
        controller.runtime.handoff_until = 1234.0
        controller.runtime.handoff_token = 7
        # A past deadline makes the sleep return immediately and reconnects.
        await controller._auto_resume(7, controller.dependencies.wall_time() - 1)
        self.assertFalse(controller.runtime.handoff_active)
        self.assertIsNone(controller.runtime.handoff_until)
        await controller.stop()

    async def test_auto_resume_ignores_stale_token(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        controller.runtime.handoff_active = True
        controller.runtime.handoff_token = 9
        # A superseded token must not resume a newer handoff.
        await controller._auto_resume(1, controller.dependencies.wall_time() - 1)
        self.assertTrue(controller.runtime.handoff_active)
        await controller.stop()

    # -- forget -------------------------------------------------------------

    async def test_forget_rejected_during_operation(self) -> None:
        controller = self.controller()
        controller.runtime.pairing = True
        result = await controller.forget()
        self.assertFalse(result["ok"])

    async def test_forget_swallows_unlink_errors(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        panel.write_json_atomic(controller.summary_file, summary())
        original_unlink = Path.unlink

        def failing_unlink(self, missing_ok=False):
            if self.name == "latest_status.json":
                raise OSError("locked")
            return original_unlink(self, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", failing_unlink):
            with self.assertLogs("weber_connect_panel", level="WARNING"):
                result = await controller.forget()
        self.assertTrue(result["ok"])
        await controller.stop()

    # -- read cycle ---------------------------------------------------------

    async def test_read_cycle_once_returns_false_when_unpaired(self) -> None:
        controller = self.controller()
        self.assertFalse(await controller._read_cycle_once())
        await controller.stop()

    async def test_read_cycle_no_probe_status(self) -> None:
        async def read_status(**_k):
            return {"connected": True, "latest_status": None}

        controller = self.paired(
            panel.ControllerDependencies(read_status=read_status)
        )
        result = await controller._read_cycle_once()
        self.assertFalse(result)
        self.assertIn("no probe status", controller.runtime.last_error)
        await controller.stop()

    async def test_record_read_failure_without_summary_skips_publish(self) -> None:
        controller = self.controller()
        result = await controller._record_read_failure("boom")
        self.assertFalse(result)
        self.assertEqual(controller.runtime.consecutive_failures, 1)
        await controller.stop()

    async def test_publish_reports_mqtt_error(self) -> None:
        class FailingSession:
            async def publish(self, state, poll_seconds):
                raise RuntimeError("broker down")

            async def close(self):
                pass

        async def read_status(**_k):
            return {
                "connected": True,
                "latest_status": {
                    "probe_count": 1,
                    "probes": [{"probe_number": 1, "state": "PROBED"}],
                },
            }

        controller = self.paired(
            panel.ControllerDependencies(
                read_status=read_status,
                mqtt_factory=lambda *a, **k: FailingSession(),
            ),
            mqtt={"host": "mqtt.local"},
        )
        with self.assertLogs("weber_connect_panel", level="ERROR"):
            await controller._read_cycle_once()
        self.assertEqual(controller.runtime.mqtt_error, "broker down")
        await controller.stop()

    async def test_close_mqtt_swallows_errors(self) -> None:
        class BadSession:
            async def close(self):
                raise RuntimeError("cannot close")

        controller = self.controller()
        controller._mqtt_session = BadSession()
        with self.assertLogs("weber_connect_panel", level="WARNING"):
            await controller._close_mqtt()
        await controller.stop()

    # -- load / derived state ----------------------------------------------

    async def test_load_reports_corrupt_files(self) -> None:
        self.data_dir.joinpath("settings.json").write_text("not json", encoding="utf-8")
        self.data_dir.joinpath("pairing_summary.json").write_text("not json", encoding="utf-8")
        self.data_dir.joinpath("handoff.json").write_text("not json", encoding="utf-8")
        with self.assertLogs("weber_connect_panel", level="WARNING"):
            controller = self.controller()
        self.assertEqual(controller.settings.poll_seconds, 10)
        await controller.stop()

    async def test_load_indefinite_handoff(self) -> None:
        panel.write_json_atomic(
            self.data_dir / "handoff.json", {"active": True, "until": None}
        )
        controller = self.controller()
        self.assertTrue(controller.runtime.handoff_active)
        self.assertIsNone(controller.runtime.handoff_until)
        await controller.stop()

    async def test_load_expired_handoff_is_cleared(self) -> None:
        panel.write_json_atomic(
            self.data_dir / "handoff.json",
            {"active": True, "until": panel.time.time() - 100},
        )
        controller = self.controller()
        self.assertFalse(controller.runtime.handoff_active)
        self.assertFalse(controller.handoff_file.exists())
        await controller.stop()

    async def test_address_falls_back_to_summary(self) -> None:
        controller = self.controller()
        controller.summary = summary()
        self.assertEqual(controller.address, ADDRESS)
        self.assertTrue(controller.paired)
        await controller.stop()

    async def test_heartbeat_is_lock_free_snapshot(self) -> None:
        controller = self.controller()
        beat = controller.heartbeat()
        self.assertTrue(beat["ok"])
        self.assertEqual(beat["state"], "setup")
        await controller.stop()

    async def test_operation_error_records_setup_error(self) -> None:
        controller = self.controller()
        controller._operation_error(RuntimeError("boom"))
        self.assertEqual(controller.runtime.setup_error, "boom")
        await controller.stop()

    async def test_fatal_error_is_recorded_and_awaited(self) -> None:
        controller = self.controller()

        async def observe() -> None:
            error = await controller.wait_for_fatal_error()
            self.assertIsInstance(error, ValueError)

        waiter = asyncio.create_task(observe())
        await asyncio.sleep(0)
        with self.assertLogs("weber_connect_panel", level="CRITICAL"):
            controller._record_fatal_error(ValueError("dead"))
        await asyncio.wait_for(waiter, timeout=1)
        await controller.stop()

    async def test_publish_reuses_existing_session(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.publishes = 0

            async def publish(self, state, poll_seconds):
                self.publishes += 1

            async def close(self):
                pass

        sessions: list[Session] = []

        def factory(*a, **k):
            session = Session()
            sessions.append(session)
            return session

        async def read_status(**_k):
            return {
                "connected": True,
                "latest_status": {
                    "probe_count": 1,
                    "probes": [{"probe_number": 1, "state": "PROBED"}],
                },
            }

        controller = self.paired(
            panel.ControllerDependencies(
                read_status=read_status, mqtt_factory=factory
            ),
            mqtt={"host": "mqtt.local"},
        )
        await controller._read_cycle_once()
        await controller._read_cycle_once()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].publishes, 2)
        await controller.stop()

    async def test_forget_without_address_still_succeeds(self) -> None:
        controller = self.controller()
        no_address = summary()
        no_address["hub"]["ble_address"] = ""
        controller.summary = no_address
        controller.settings = controller.settings.with_address(None)
        # summary present but no usable address → the release branch is skipped.
        result = await controller.forget()
        self.assertTrue(result["ok"])
        self.assertIsNone(controller.summary)
        await controller.stop()

    async def test_resume_cancels_pending_auto_resume(self) -> None:
        controller = self.paired(
            panel.ControllerDependencies(release=lambda _a: True)
        )
        await controller.handoff(5)
        task = controller._auto_resume_task
        self.assertIsNotNone(task)
        await controller.resume()
        self.assertIsNone(controller._auto_resume_task)
        for _ in range(5):
            if task.cancelled() or task.done():
                break
            await asyncio.sleep(0)
        self.assertTrue(task.cancelled() or task.done())
        await controller.stop()

    # -- bridge loop --------------------------------------------------------

    async def test_bridge_loop_polls_paired_hub(self) -> None:
        reads = asyncio.Event()

        async def read_status(**_k):
            reads.set()
            return {
                "connected": True,
                "latest_status": {
                    "probe_count": 1,
                    "probes": [{"probe_number": 1, "state": "PROBED"}],
                },
            }

        controller = self.paired(
            panel.ControllerDependencies(
                read_status=read_status,
                release=lambda _a: True,
                jitter=lambda _l, _h: 0.0,
            )
        )
        controller.settings = controller.settings.updated({"poll_seconds": 10})
        await controller.start()
        await asyncio.wait_for(reads.wait(), timeout=1)
        self.assertTrue(controller.runtime.last_read_ok)
        await controller.stop()

    async def test_bridge_loop_retries_after_failure(self) -> None:
        failed = asyncio.Event()

        async def read_status(**_k):
            failed.set()
            raise RuntimeError("radio down")

        controller = self.paired(
            panel.ControllerDependencies(
                read_status=read_status,
                release=lambda _a: True,
                jitter=lambda _l, _h: 0.0,
            )
        )
        controller.settings = controller.settings.updated({"poll_seconds": 10})
        await controller.start()
        await asyncio.wait_for(failed.wait(), timeout=2)
        # Give the loop a tick to compute the retry backoff after the failure.
        while controller.runtime.next_retry_seconds is None:
            await asyncio.sleep(0)
        self.assertGreaterEqual(controller.runtime.consecutive_failures, 1)
        self.assertGreaterEqual(controller.runtime.next_retry_seconds, 0)
        await controller.stop()


if __name__ == "__main__":
    unittest.main()

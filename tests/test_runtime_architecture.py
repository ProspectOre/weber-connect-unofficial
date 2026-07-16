from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_panel as panel  # noqa: E402
from weber_persistence import read_json, write_json_atomic  # noqa: E402
from weber_runtime import BridgeSettings, TaskSupervisor, retry_delay  # noqa: E402

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


class FakeMqttSession:
    def __init__(self) -> None:
        self.published: list[dict] = []
        self.closed = False

    async def publish(self, state: dict, poll_seconds: int) -> None:
        self.published.append({"state": state, "poll_seconds": poll_seconds})

    async def close(self) -> None:
        self.closed = True


class RuntimeArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def paired_controller(
        self,
        dependencies: panel.ControllerDependencies,
        *,
        mqtt: dict | None = None,
    ) -> panel.HubController:
        controller = panel.HubController(
            data_dir=self.data_dir,
            mqtt=mqtt,
            dependencies=dependencies,
        )
        controller.summary = summary()
        controller.settings = controller.settings.with_address(ADDRESS)
        return controller

    async def test_scan_is_single_flight_from_acceptance(self) -> None:
        started = asyncio.Event()
        finish = asyncio.Event()

        async def scan(*_args, **_kwargs):
            started.set()
            await finish.wait()
            return {"weber_candidates": []}

        controller = panel.HubController(
            self.data_dir,
            mqtt=None,
            dependencies=panel.ControllerDependencies(scan=scan),
        )
        first = await controller.start_scan()
        second = await controller.start_scan()

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        await started.wait()
        finish.set()
        while controller.runtime.scanning:
            await asyncio.sleep(0)
        await controller.stop()

    async def test_failing_status_write_is_recoverable_not_fatal(self) -> None:
        async def read_status(**_kwargs):
            return {
                "connected": True,
                "latest_status": {
                    "kind": "cook_session_status",
                    "probe_count": 1,
                    "probes": [{"probe_number": 1, "state": "PROBED"}],
                },
            }

        controller = self.paired_controller(
            panel.ControllerDependencies(read_status=read_status)
        )
        with mock.patch.object(
            panel, "write_json_atomic", side_effect=OSError("disk full")
        ):
            # A status-write failure must be swallowed as a recoverable read
            # failure rather than propagating and killing the runtime.
            result = await controller._read_cycle_once()

        self.assertFalse(result)
        self.assertFalse(controller.runtime.last_read_ok)
        self.assertEqual(controller.runtime.consecutive_failures, 1)
        self.assertIn("Could not save status", controller.runtime.last_error or "")
        await controller.stop()

    async def test_scan_normalizes_candidates(self) -> None:
        async def scan(*_args, **_kwargs):
            return {
                "weber_candidates": [
                    {"address": ADDRESS, "local_name": "Kitchen Hub", "rssi": -54},
                    {"address": None, "name": "Invalid"},
                ]
            }

        controller = panel.HubController(
            self.data_dir,
            mqtt=None,
            dependencies=panel.ControllerDependencies(scan=scan),
        )
        await controller.start_scan()
        while controller.runtime.scanning:
            await asyncio.sleep(0)

        self.assertEqual(
            controller.runtime.candidates,
            [{"address": ADDRESS, "name": "Kitchen Hub", "rssi": -54}],
        )
        self.assertIsNone(controller.runtime.setup_error)
        await controller.stop()

    async def test_pairing_success_persists_typed_state(self) -> None:
        keys = {
            "companion_id": COMPANION_ID,
            "companion_public_key": "aa" * 64,
            "display_name": "Home Assistant",
        }

        async def pair_once(_args, received_keys):
            self.assertIs(received_keys, keys)
            return {"pairing_response": {"status": "CONFIRMED"}}

        dependencies = panel.ControllerDependencies(
            pair=pair_once,
            key_loader=lambda **_kwargs: keys,
            summary_builder=lambda **_kwargs: summary(),
        )
        controller = panel.HubController(
            self.data_dir,
            mqtt=None,
            dependencies=dependencies,
        )

        accepted = await controller.pair(ADDRESS)
        while controller.runtime.pairing:
            await asyncio.sleep(0)

        self.assertTrue(accepted["ok"])
        self.assertTrue(controller.paired)
        self.assertEqual(controller.settings.address, ADDRESS)
        self.assertEqual(read_json(controller.summary_file)["companion_id"], COMPANION_ID)
        self.assertEqual(read_json(controller.settings_file)["address"], ADDRESS)
        await controller.stop()

    async def test_shutdown_cancels_runtime_and_releases_ble(self) -> None:
        read_started = asyncio.Event()
        released: list[str] = []

        async def read_status(**_kwargs):
            read_started.set()
            await asyncio.Event().wait()

        dependencies = panel.ControllerDependencies(
            read_status=read_status,
            release=lambda address: released.append(address) is None,
            jitter=lambda _low, _high: 0.0,
        )
        controller = self.paired_controller(dependencies)
        await controller.start()
        await asyncio.wait_for(read_started.wait(), timeout=1)

        await controller.stop()

        self.assertEqual(released, [ADDRESS])
        self.assertEqual(controller._supervisor.task_count, 0)

    async def test_failure_publishes_disconnected_state_and_keeps_last_good_ui_state(self) -> None:
        fake_mqtt = FakeMqttSession()

        async def read_status(**_kwargs):
            raise RuntimeError("radio unavailable")

        dependencies = panel.ControllerDependencies(
            read_status=read_status,
            mqtt_factory=lambda *_args, **_kwargs: fake_mqtt,
        )
        controller = self.paired_controller(
            dependencies,
            mqtt={"host": "mqtt.local", "port": 1883},
        )
        controller.runtime.last_good_state = {
            "probe_count": 1,
            "probes": [{"probe_number": 1, "probe_temp_f": 205}],
        }

        with self.assertLogs("weber_connect_panel", level="WARNING"):
            success = await controller._read_cycle()
        snapshot = await controller.snapshot()

        self.assertFalse(success)
        self.assertTrue(snapshot["readings_stale"])
        self.assertEqual(snapshot["probes"][0]["probe_temp_f"], 205)
        self.assertFalse(fake_mqtt.published[-1]["state"]["connected"])
        self.assertEqual(fake_mqtt.published[-1]["state"]["probe_count"], 0)
        persisted = read_json(controller.status_file)
        self.assertFalse(persisted["connected"])
        await controller.stop()

    async def test_successful_read_persists_and_publishes_live_state(self) -> None:
        fake_mqtt = FakeMqttSession()
        latest = {
            "probe_count": 1,
            "probes": [
                {
                    "probe_number": 1,
                    "probe_temp_f": 205.5,
                    "probe_temp_c": 96.4,
                    "state": "PROBED",
                    "battery_level": 88,
                }
            ],
        }

        async def read_status(**_kwargs):
            return {"connected": True, "latest_status": latest}

        controller = self.paired_controller(
            panel.ControllerDependencies(
                read_status=read_status,
                mqtt_factory=lambda *_args, **_kwargs: fake_mqtt,
            ),
            mqtt={"host": "mqtt.local"},
        )

        success = await controller._read_cycle()
        snapshot = await controller.snapshot()

        self.assertTrue(success)
        self.assertEqual(snapshot["state"], "online")
        self.assertFalse(snapshot["readings_stale"])
        self.assertEqual(snapshot["probes"][0]["probe_temp_f"], 205.5)
        self.assertTrue(fake_mqtt.published[-1]["state"]["connected"])
        self.assertTrue(read_json(controller.status_file)["connected"])
        await controller.stop()

    async def test_handoff_survives_restart_until_manual_resume(self) -> None:
        dependencies = panel.ControllerDependencies(release=lambda _address: True)
        controller = self.paired_controller(dependencies)
        write_json_atomic(controller.summary_file, summary())
        controller._save_settings()

        result = await controller.handoff(0)
        reloaded = panel.HubController(
            self.data_dir,
            mqtt=None,
            dependencies=dependencies,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(reloaded.state(), "handoff")
        await reloaded.resume()
        self.assertFalse(reloaded.handoff_file.exists())
        await controller.stop()
        await reloaded.stop()

    async def test_timed_handoff_reschedules_after_restart(self) -> None:
        read_started = asyncio.Event()

        async def read_status(**_kwargs):
            read_started.set()
            await asyncio.Event().wait()

        dependencies = panel.ControllerDependencies(
            read_status=read_status,
            release=lambda _address: True,
        )
        controller = self.paired_controller(dependencies)
        write_json_atomic(controller.summary_file, summary())
        controller._save_settings()
        write_json_atomic(
            controller.handoff_file,
            {"active": True, "until": panel.time.time() + 0.05},
        )
        reloaded = panel.HubController(
            self.data_dir,
            mqtt=None,
            dependencies=dependencies,
        )

        await reloaded.start()
        await asyncio.wait_for(read_started.wait(), timeout=1)

        self.assertFalse(reloaded.runtime.handoff_active)
        self.assertFalse(reloaded.handoff_file.exists())
        await reloaded.stop()

    async def test_forget_waits_for_in_flight_ble_read(self) -> None:
        read_started = asyncio.Event()
        finish_read = asyncio.Event()
        released: list[str] = []

        async def read_status(**_kwargs):
            read_started.set()
            await finish_read.wait()
            return {"connected": True, "latest_status": {"probe_count": 0, "probes": []}}

        controller = self.paired_controller(
            panel.ControllerDependencies(
                read_status=read_status,
                release=lambda address: released.append(address) is None,
            )
        )
        await controller.start()
        await read_started.wait()
        forgetting = asyncio.create_task(controller.forget())
        await asyncio.sleep(0)
        self.assertFalse(forgetting.done())

        finish_read.set()
        result = await forgetting

        self.assertTrue(result["ok"])
        self.assertEqual(released, [ADDRESS])
        self.assertFalse(controller.paired)
        await controller.stop()

    async def test_unknown_setting_is_rejected_atomically(self) -> None:
        controller = panel.HubController(self.data_dir, mqtt=None)
        result = await controller.update_settings({"poll_seconds": 60, "mystery": 1})
        self.assertFalse(result["ok"])
        self.assertEqual(controller.settings, BridgeSettings())
        await controller.stop()


class RuntimePrimitiveTests(unittest.TestCase):
    def test_retry_delay_is_bounded_exponential(self) -> None:
        self.assertEqual(retry_delay(30, 1), 30)
        self.assertEqual(retry_delay(30, 2), 60)
        self.assertEqual(retry_delay(30, 5), 300)
        self.assertEqual(retry_delay(30, 99), 300)

    def test_durable_json_writes_are_private_and_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            threads = [
                threading.Thread(target=write_json_atomic, args=(path, {"value": value}))
                for value in range(20)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertIn(read_json(path)["value"], range(20))
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_task_supervisor_cancels_owned_tasks(self) -> None:
        async def scenario() -> None:
            cancelled = asyncio.Event()

            async def worker() -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            supervisor = TaskSupervisor()
            supervisor.spawn("worker", worker())
            await asyncio.sleep(0)
            await supervisor.close()
            self.assertTrue(cancelled.is_set())
            self.assertEqual(supervisor.task_count, 0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_persistence as persistence  # noqa: E402
from weber_persistence import read_json, write_json_atomic  # noqa: E402
from weber_runtime import (  # noqa: E402
    BridgeSettings,
    TaskSupervisor,
    parse_whole_number,
)


class ReadJsonTests(unittest.TestCase):
    def test_non_object_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_json(path)


class WriteJsonAtomicTests(unittest.TestCase):
    def test_writes_private_file_with_mode_600(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            write_json_atomic(path, {"value": 1})
            self.assertEqual(read_json(path)["value"], 1)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_directory_fsync_failure_is_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            real_open = os.open

            def fake_open(target, flags, *args, **kwargs):
                if Path(target) == path.parent and flags == os.O_RDONLY:
                    raise OSError("cannot open directory")
                return real_open(target, flags, *args, **kwargs)

            with mock.patch.object(persistence.os, "open", side_effect=fake_open):
                write_json_atomic(path, {"value": 2})
            # The write still lands even though the directory fsync was skipped.
            self.assertEqual(read_json(path)["value"], 2)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])


class ParseWholeNumberTests(unittest.TestCase):
    def test_bool_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_whole_number(True, "flag")

    def test_unsupported_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_whole_number([1], "list")

    def test_non_integer_float_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_whole_number(1.5, "temp")

    def test_integer_float_is_accepted(self) -> None:
        self.assertEqual(parse_whole_number(2.0, "temp"), 2)

    def test_non_canonical_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_whole_number("01", "count")

    def test_canonical_string_is_accepted(self) -> None:
        self.assertEqual(parse_whole_number("42", "count"), 42)


class BridgeSettingsTests(unittest.TestCase):
    def test_non_string_address_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BridgeSettings.from_mapping({"address": 1234})

    def test_updated_only_poll_seconds_leaves_handoff(self) -> None:
        settings = BridgeSettings(poll_seconds=30, handoff_minutes=15)
        updated = settings.updated({"poll_seconds": 90})
        self.assertEqual(updated.poll_seconds, 90)
        self.assertEqual(updated.handoff_minutes, 15)

    def test_updated_only_handoff_minutes_leaves_poll(self) -> None:
        settings = BridgeSettings(poll_seconds=30, handoff_minutes=15)
        updated = settings.updated({"handoff_minutes": 20})
        self.assertEqual(updated.poll_seconds, 30)
        self.assertEqual(updated.handoff_minutes, 20)

    def test_updated_rejects_unknown_key(self) -> None:
        with self.assertRaises(ValueError):
            BridgeSettings().updated({"mystery": 1})


class TaskSupervisorTests(unittest.TestCase):
    def test_spawn_after_close_is_rejected(self) -> None:
        async def scenario() -> None:
            supervisor = TaskSupervisor()
            await supervisor.close()
            with self.assertRaises(RuntimeError):
                supervisor.spawn("late", asyncio.sleep(0))

        asyncio.run(scenario())

    def test_on_error_callback_receives_task_failure(self) -> None:
        async def scenario() -> None:
            captured: list[BaseException] = []

            async def boom() -> None:
                raise ValueError("kaboom")

            supervisor = TaskSupervisor()
            supervisor.spawn("boom", boom(), on_error=captured.append)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await supervisor.close()
            self.assertEqual(len(captured), 1)
            self.assertIsInstance(captured[0], ValueError)

        asyncio.run(scenario())

    def test_default_error_handler_logs(self) -> None:
        async def scenario() -> None:
            async def boom() -> None:
                raise ValueError("kaboom")

            supervisor = TaskSupervisor()
            with self.assertLogs("weber_connect_runtime", level="ERROR"):
                supervisor.spawn("boom", boom())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            await supervisor.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

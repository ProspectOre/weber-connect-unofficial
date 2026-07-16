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

import weber_ble_pair as pair  # noqa: E402
from saber_frames import build_command_frame  # noqa: E402

COMPANION_ID = "00112233445566778899aabbccddeeff"
COMPANION_PUBLIC_KEY = "aa" * 64


def greeting_frame(type_value: int, version: int = 11) -> bytes:
    return build_command_frame(5, version, type_value, b"")


def error_frame(version: int) -> bytes:
    # tag 0 (error_type) length 1 value 0 -> UNSUPPORTED_MESSAGE_VERSION
    return build_command_frame(3, version, 0x87, bytes([0, 1, 0]))


def pairing_response_frame(status: int = 0x00) -> bytes:
    payload = bytes(range(16)) + bytes(range(64)) + bytes([status])
    return build_command_frame(7, 10, 0x85, payload)


def make_pair_client(responses: list[bytes]):
    class FakeClient:
        def __init__(self, address, timeout):
            self.address = address
            self.timeout = timeout
            self.is_connected = True
            self.responses = list(responses)
            self.writes: list = []
            self.notified: dict = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.is_connected = False
            return False

        async def start_notify(self, uuid, callback):
            self.notified[uuid] = callback

        async def stop_notify(self, _uuid):
            return None

        async def write_gatt_char(self, uuid, data, response=True):
            self.writes.append((uuid, bytes(data), response))

        async def read_gatt_char(self, uuid):
            if uuid == pair.RESPONSE_UUID and self.responses:
                return self.responses.pop(0)
            return b""

    return FakeClient


def pair_args(**overrides) -> SimpleNamespace:
    values = {
        "address": "AA:BB:CC:DD:EE:FF",
        "timeout": 20.0,
        "write_without_response": False,
        "version": 11,
        "listen_seconds": 5.0,
        "display_name": "Home Assistant",
        "hub_name": "Weber Connect Hub",
        "hub_serial": "TESTSERIAL",
        "hub_model": "Connect Hub",
        "hub_software_revision": "1.2.3",
        "hub_wifi_mac": None,
        "companion_id": None,
        "companion_public_key": None,
        "reset_key": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def sample_keys() -> dict:
    return {
        "display_name": "Home Assistant",
        "companion_id": COMPANION_ID,
        "companion_public_key": COMPANION_PUBLIC_KEY,
        "companion_private_key": "bb" * 32,
    }


class PureHelperTests(unittest.TestCase):
    def test_normalize_hex_accepts_and_rejects(self) -> None:
        self.assertEqual(pair.normalize_hex("AA:BB", 2, "x"), "aabb")
        with self.assertRaises(ValueError):
            pair.normalize_hex("zz", 2, "companion id")

    def test_generate_companion_keypair_shapes(self) -> None:
        private_hex, public_hex = pair.generate_companion_keypair()
        self.assertEqual(len(private_hex), 64)
        self.assertEqual(len(public_hex), 128)

    def test_generate_pairing_keys_structure(self) -> None:
        keys = pair.generate_pairing_keys("Home Assistant")
        self.assertEqual(len(keys["companion_id"]), 32)
        self.assertEqual(keys["display_name"], "Home Assistant")
        self.assertEqual(len(keys["companion_public_key"]), 128)

    def test_make_event_decodes_frame(self) -> None:
        event = pair.make_event("sender", greeting_frame(0xF1), "response")
        self.assertEqual(event["source"], "response")
        self.assertIn("decoded", event)
        self.assertEqual(event["length"], len(greeting_frame(0xF1)))

    def test_extract_pairing_response_ignores_non_pairing(self) -> None:
        event = pair.make_event("sender", greeting_frame(0xF1), "response")
        self.assertIsNone(pair.extract_pairing_response(event))


class LoadOrCreateKeysTests(unittest.TestCase):
    def test_creates_new_key_file_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            keys = pair.load_or_create_pairing_keys(
                path, "Home Assistant", None, None, reset_key=False
            )
            self.assertTrue(path.exists())
            self.assertEqual(len(keys["companion_id"]), 32)
            # Written file is reloaded and normalized on the second call.
            again = pair.load_or_create_pairing_keys(
                path, "Home Assistant", None, None, reset_key=False
            )
            self.assertEqual(again["companion_id"], keys["companion_id"])

    def test_reset_key_regenerates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            first = pair.load_or_create_pairing_keys(
                path, "Home Assistant", None, None, reset_key=False
            )
            second = pair.load_or_create_pairing_keys(
                path, "Home Assistant", None, None, reset_key=True
            )
            self.assertNotEqual(first["companion_id"], second["companion_id"])

    def test_overrides_and_legacy_keypair_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            # Legacy file without a real private key triggers in-place upgrade.
            path.write_text(
                json.dumps({"companion_id": COMPANION_ID, "display_name": "Legacy"}),
                encoding="utf-8",
            )
            keys = pair.load_or_create_pairing_keys(
                path, "Home Assistant", None, None, reset_key=False
            )
            self.assertEqual(len(keys["companion_private_key"]), 64)
            self.assertEqual(keys["display_name"], "Legacy")

    def test_explicit_companion_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.json"
            keys = pair.load_or_create_pairing_keys(
                path,
                "Home Assistant",
                COMPANION_ID,
                COMPANION_PUBLIC_KEY,
                reset_key=False,
            )
            self.assertEqual(keys["companion_id"], COMPANION_ID)
            self.assertEqual(keys["companion_public_key"], COMPANION_PUBLIC_KEY)


class PairOnceTests(unittest.TestCase):
    def run_pair(self, responses, **arg_overrides):
        fake = make_pair_client(responses)
        args = pair_args(**arg_overrides)
        with mock.patch.dict(
            sys.modules, {"bleak": SimpleNamespace(BleakClient=fake)}
        ):
            return asyncio.run(pair.pair_once(args, sample_keys()))

    def test_pairing_required_then_confirmed(self) -> None:
        result = self.run_pair(
            [greeting_frame(0xF1), pairing_response_frame(0x00)]
        )
        self.assertEqual(result["pairing_response"]["status"], "CONFIRMED")
        self.assertTrue(result["connected"])

    def test_handshake_success_after_version_switch(self) -> None:
        # First greeting is rejected with a different hub version, second
        # greeting is accepted (0xF2), then pairing confirms.
        result = self.run_pair(
            [error_frame(10), greeting_frame(0xF2, version=10), pairing_response_frame(0x00)]
        )
        self.assertEqual(result["message_version"], 10)
        self.assertEqual(result["pairing_response"]["status"], "CONFIRMED")

    def test_no_pairing_response_within_listen_window(self) -> None:
        result = self.run_pair([greeting_frame(0xF1)], listen_seconds=0.0)
        self.assertIsNone(result["pairing_response"])

    def test_import_error_when_bleak_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaises(RuntimeError):
                asyncio.run(pair.pair_once(pair_args(), sample_keys()))

    def test_resilient_to_subscribe_session_and_read_failures(self) -> None:
        # A fake that exercises the warning/exception branches: one failed
        # subscription, a notification-delivered pairing response, a failed
        # session claim, and a raising response read.
        class GrumpyClient:
            def __init__(self, address, timeout):
                self.is_connected = True
                self._read_calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                self.is_connected = False
                return False

            async def start_notify(self, uuid, callback):
                if uuid == pair.RESPONSE_UUID:
                    raise RuntimeError("cannot subscribe")
                # Deliver the greeting via the notification characteristic.
                callback("sender", bytearray(greeting_frame(0xF1)))

            async def stop_notify(self, _uuid):
                raise RuntimeError("stop failed")

            async def write_gatt_char(self, uuid, data, response=True):
                if uuid == pair.SESSION_UUID:
                    raise RuntimeError("cannot claim session")

            async def read_gatt_char(self, uuid):
                self._read_calls += 1
                if self._read_calls == 1:
                    raise RuntimeError("read failed")
                if self._read_calls == 2:
                    return pairing_response_frame(0x00)
                return b""

        args = pair_args(listen_seconds=5.0)
        with mock.patch.dict(
            sys.modules, {"bleak": SimpleNamespace(BleakClient=GrumpyClient)}
        ):
            result = asyncio.run(pair.pair_once(args, sample_keys()))
        self.assertEqual(result["pairing_response"]["status"], "CONFIRMED")


class AsyncMainTests(unittest.TestCase):
    def bare_args(self, tmp: Path, **overrides) -> SimpleNamespace:
        base = pair_args(
            pairing_key_file=tmp / "keys.json",
            json_out=tmp / "result.json",
            pairing_summary_out=tmp / "summary.json",
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_async_main_writes_summary_on_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            args = self.bare_args(tmp_path)
            fake = make_pair_client(
                [greeting_frame(0xF1), pairing_response_frame(0x00)]
            )
            with mock.patch.dict(
                sys.modules, {"bleak": SimpleNamespace(BleakClient=fake)}
            ):
                rc = asyncio.run(pair.async_main(args))
            self.assertEqual(rc, 0)
            self.assertTrue(args.pairing_summary_out.exists())
            summary = json.loads(args.pairing_summary_out.read_text())
            self.assertEqual(summary["pairing_response"]["status"], "CONFIRMED")

    def test_async_main_returns_2_without_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.bare_args(Path(tmp))
            with mock.patch.object(
                pair, "pair_once", return_value={"pairing_response": None}
            ):
                rc = asyncio.run(pair.async_main(args))
            self.assertEqual(rc, 2)

    def test_async_main_returns_3_when_not_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.bare_args(Path(tmp))
            with mock.patch.object(
                pair,
                "pair_once",
                return_value={"pairing_response": {"status": "REJECTED"}},
            ):
                rc = asyncio.run(pair.async_main(args))
            self.assertEqual(rc, 3)


class LifecycleTests(unittest.TestCase):
    def test_pair_until_stopped_returns_result(self) -> None:
        args = pair_args()

        async def fake_main(_args):
            return 0

        with mock.patch.object(pair, "async_main", side_effect=fake_main):
            rc = asyncio.run(pair.pair_until_stopped(args))
        self.assertEqual(rc, 0)

    def test_pair_until_stopped_propagates_uncommanded_cancel(self) -> None:
        args = pair_args()

        async def fake_main(_args):
            raise asyncio.CancelledError()

        with mock.patch.object(pair, "async_main", side_effect=fake_main):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(pair.pair_until_stopped(args))

    def test_main_parses_args_and_runs(self) -> None:
        argv = [
            "weber_ble_pair",
            "--address",
            "AA:BB:CC:DD:EE:FF",
            "--log-level",
            "warning",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(pair, "pair_until_stopped", new=mock.Mock(return_value=None)),
            mock.patch.object(pair.asyncio, "run", return_value=0) as run,
        ):
            rc = pair.main()
        self.assertEqual(rc, 0)
        self.assertTrue(run.called)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "weber_connect_ble" / "app"
sys.path.insert(0, str(APP))

import weber_ble_scan as scanner  # noqa: E402


def adv(**kwargs) -> SimpleNamespace:
    base = {
        "local_name": None,
        "rssi": -60,
        "manufacturer_data": {},
        "service_uuids": [],
        "service_data": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


class HelperTests(unittest.TestCase):
    def test_bytes_to_hex_handles_none(self) -> None:
        self.assertIsNone(scanner.bytes_to_hex(None))
        self.assertEqual(scanner.bytes_to_hex(b"\x01\x02"), "01:02")

    def test_utc_now_is_isoformat(self) -> None:
        self.assertIn("T", scanner.utc_now())

    def test_is_weber_by_name_token_and_negatives(self) -> None:
        self.assertTrue(scanner.is_weber("June Oven", {}))
        self.assertFalse(scanner.is_weber(None, {}))
        self.assertFalse(scanner.is_weber("Random Speaker", {}))

    def test_make_record_falls_back_to_device_fields(self) -> None:
        device = SimpleNamespace(address="AA:BB", name="DeviceName", rssi=-77)
        record = scanner.make_record(device, adv(local_name=None, rssi=None))
        self.assertEqual(record["local_name"], "DeviceName")
        self.assertEqual(record["rssi"], -77)
        self.assertFalse(record["is_weber_candidate"])

    def test_log_record_skips_non_candidate_without_include_all(self) -> None:
        record = scanner.make_record(
            SimpleNamespace(address="AA:BB", name="Phone", rssi=-50), adv()
        )
        with mock.patch.object(scanner.LOGGER, "info") as info:
            scanner.log_record(record, include_all=False)
        info.assert_not_called()

    def test_log_record_emits_for_candidate_with_manufacturer_data(self) -> None:
        record = scanner.make_record(
            SimpleNamespace(address="AA:BB", name="Weber", rssi=-50),
            adv(local_name="Weber Connect", manufacturer_data={0x0DF2: b"\x01\x02"}),
        )
        with mock.patch.object(scanner.LOGGER, "info") as info:
            scanner.log_record(record, include_all=True)
        self.assertGreaterEqual(info.call_count, 2)


class FakeScanner:
    def __init__(self, callback, emissions):
        self.callback = callback
        self.emissions = emissions
        self.stopped = False

    async def start(self):
        for device, advertisement in self.emissions:
            self.callback(device, advertisement)

    async def stop(self):
        self.stopped = True


class ScanTests(unittest.TestCase):
    def _run(self, emissions, **kwargs):
        def factory(callback):
            return FakeScanner(callback, emissions)

        with mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakScanner=factory)}):
            return asyncio.run(scanner.scan(**kwargs))

    def test_scan_orders_candidates_first_then_rssi(self) -> None:
        emissions = [
            (SimpleNamespace(address="OTHER", name="Phone", rssi=-40), adv(rssi=-40)),
            (
                SimpleNamespace(address="WEBER", name="Weber", rssi=-70),
                adv(local_name="Weber Connect", rssi=-70),
            ),
            (SimpleNamespace(address=None, name="Ghost", rssi=-10), adv(rssi=-10)),
        ]
        result = self._run(emissions, timeout=1, include_all=True, stop_on_weber=False)
        self.assertEqual(result["records"][0]["address"], "WEBER")
        self.assertEqual(len(result["weber_candidates"]), 1)

    def test_scan_stops_on_weber(self) -> None:
        emissions = [
            (
                SimpleNamespace(address="WEBER", name="Weber", rssi=-50),
                adv(local_name="Weber Connect"),
            )
        ]
        result = self._run(emissions, timeout=5, include_all=False, stop_on_weber=True)
        self.assertEqual(result["weber_candidates"][0]["address"], "WEBER")

    def test_scan_times_out_without_weber(self) -> None:
        emissions = [
            (SimpleNamespace(address="OTHER", name="Phone", rssi=-40), adv(rssi=-40))
        ]
        result = self._run(emissions, timeout=0.01, include_all=False, stop_on_weber=True)
        self.assertEqual(result["weber_candidates"], [])

    def test_scan_raises_when_bleak_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaises(RuntimeError):
                asyncio.run(scanner.scan(timeout=1, include_all=False, stop_on_weber=True))


class AsyncMainTests(unittest.TestCase):
    def _args(self, json_out):
        return SimpleNamespace(
            timeout=1,
            include_all=False,
            no_stop=False,
            json_out=json_out,
        )

    def test_async_main_success(self) -> None:
        out = Path("/tmp/weber-scan-test.json")
        fake = {"weber_candidates": [{"address": "WEBER", "rssi": -50, "local_name": "Weber"}]}
        with (
            mock.patch.object(scanner, "scan", new=mock.AsyncMock(return_value=fake)),
            mock.patch.object(scanner, "write_json_atomic") as writer,
        ):
            code = asyncio.run(scanner.async_main(self._args(out)))
        self.assertEqual(code, 0)
        writer.assert_called_once()

    def test_async_main_no_candidates_returns_two(self) -> None:
        out = Path("/tmp/weber-scan-test.json")
        with (
            mock.patch.object(scanner, "scan", new=mock.AsyncMock(return_value={"weber_candidates": []})),
            mock.patch.object(scanner, "write_json_atomic") as writer,
        ):
            code = asyncio.run(scanner.async_main(self._args(out)))
        self.assertEqual(code, 2)
        writer.assert_called_once()

    def test_write_json_atomic_delegates(self) -> None:
        with mock.patch.object(scanner, "write_private_json_atomic") as writer:
            scanner.write_json_atomic(Path("/tmp/x.json"), {"a": 1})
        writer.assert_called_once()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()

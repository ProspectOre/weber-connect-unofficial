#!/usr/bin/env python3
"""Scan for Weber Connect BLE advertisements."""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weber_persistence import write_json_atomic as write_private_json_atomic

WEBER_COMPANY_IDS = {
    0x0DF2: "Weber",
    0x07C5: "June/legacy",
}
LOGGER = logging.getLogger("weber_connect_ble_scan")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bytes_to_hex(data: bytes | bytearray | memoryview | None) -> str | None:
    if data is None:
        return None
    return bytes(data).hex(":")


def is_weber(local_name: str | None, manufacturer_data: dict[int, bytes]) -> bool:
    if any(company_id in WEBER_COMPANY_IDS for company_id in manufacturer_data):
        return True
    if not local_name:
        return False
    return any(token in local_name.lower() for token in ("weber", "connect", "june"))


def make_record(device: Any, adv: Any) -> dict[str, Any]:
    manufacturer_data = dict(getattr(adv, "manufacturer_data", {}) or {})
    service_data = dict(getattr(adv, "service_data", {}) or {})
    local_name = getattr(adv, "local_name", None) or getattr(device, "name", None)
    rssi = getattr(adv, "rssi", None)
    if rssi is None:
        rssi = getattr(device, "rssi", None)

    return {
        "seen_at": utc_now(),
        "address": getattr(device, "address", None),
        "name": getattr(device, "name", None),
        "local_name": local_name,
        "rssi": rssi,
        "is_weber_candidate": is_weber(local_name, manufacturer_data),
        "manufacturer_data": {
            f"0x{company_id:04x}": {
                "label": WEBER_COMPANY_IDS.get(company_id, "unknown"),
                "hex": bytes_to_hex(payload),
                "length": len(payload),
            }
            for company_id, payload in sorted(manufacturer_data.items())
        },
        "service_uuids": sorted(getattr(adv, "service_uuids", []) or []),
        "service_data": {uuid: bytes_to_hex(payload) for uuid, payload in sorted(service_data.items())},
    }


def log_record(record: dict[str, Any], include_all: bool) -> None:
    if not record["is_weber_candidate"] and not include_all:
        return

    marker = "WEBER" if record["is_weber_candidate"] else "OTHER"
    LOGGER.info(
        "%s candidate address=%s rssi=%s name=%s",
        marker,
        record.get("address"),
        record.get("rssi"),
        record.get("local_name") or record.get("name"),
    )
    for company_id, details in record["manufacturer_data"].items():
        LOGGER.info(
            "  manufacturer %s %s: %s",
            company_id,
            details["label"],
            details["hex"],
        )


async def scan(timeout: float, include_all: bool, stop_on_weber: bool) -> dict[str, Any]:
    try:
        from bleak import BleakScanner
    except ImportError as exc:
        raise RuntimeError("bleak is not installed; run pip install -r requirements.txt") from exc

    records: dict[str, dict[str, Any]] = {}
    printed: set[str] = set()
    found_weber = asyncio.Event()

    def detection_callback(device: Any, adv: Any) -> None:
        record = make_record(device, adv)
        address = record["address"]
        if not address:
            return
        records[address] = record

        if (record["is_weber_candidate"] or include_all) and address not in printed:
            log_record(record, include_all)
            printed.add(address)

        if record["is_weber_candidate"]:
            found_weber.set()

    scanner = BleakScanner(detection_callback)
    await scanner.start()
    try:
        if stop_on_weber:
            try:
                await asyncio.wait_for(found_weber.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(timeout)
    finally:
        await scanner.stop()

    ordered = sorted(
        records.values(),
        key=lambda item: (not item["is_weber_candidate"], -(item["rssi"] or -999)),
    )
    return {
        "scanned_at": utc_now(),
        "platform": platform.platform(),
        "timeout": timeout,
        "records": ordered,
        "weber_candidates": [item for item in ordered if item["is_weber_candidate"]],
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_private_json_atomic(path, payload)


async def async_main(args: argparse.Namespace) -> int:
    LOGGER.info("Scanning for Weber Connect BLE advertisements for %ss", args.timeout)
    LOGGER.info("Wake the Weber hub and keep it near the Home Assistant Bluetooth adapter.")
    result = await scan(args.timeout, args.include_all, not args.no_stop)
    write_json_atomic(args.json_out, result)

    candidates = result["weber_candidates"]
    if not candidates:
        LOGGER.warning("No Weber BLE advertisement was seen; wrote %s", args.json_out)
        return 2

    LOGGER.info("Found %d Weber candidate(s); wrote %s", len(candidates), args.json_out)
    for index, candidate in enumerate(candidates, start=1):
        LOGGER.info(
            "Candidate %d: address=%s rssi=%s name=%s",
            index,
            candidate.get("address"),
            candidate.get("rssi"),
            candidate.get("local_name") or candidate.get("name"),
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for Weber Connect BLE advertisements.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--include-all", action="store_true", help="Log first-seen non-Weber devices too.")
    parser.add_argument("--no-stop", action="store_true", help="Keep scanning after the first Weber candidate.")
    parser.add_argument("--json-out", type=Path, default=Path("weber_scan_latest.json"))
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

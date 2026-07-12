#!/usr/bin/env python3
"""Pair a Weber Connect Hub as a trusted local BLE companion."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import secrets
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from saber_frames import (
    COMMAND_UUID,
    NOTIFICATION_UUID,
    RESPONSE_UUID,
    SESSION_UUID,
    STATUS_UUID,
    build_command_frame,
    build_handshake_body,
    build_pairing_body,
    bytes_to_hex,
    decode_hex_frame,
)
from weber_persistence import write_json_atomic as write_private_json_atomic

DEFAULT_KEY_FILE = Path("weber_pairing_keys.json")
DEFAULT_RESULT_OUT = Path("weber_pair_latest.json")
DEFAULT_SUMMARY_OUT = Path("weber_pairing_summary.json")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
LOGGER = logging.getLogger("weber_connect_pair")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_hex(value: str, expected_bytes: int, label: str) -> str:
    normalized = value.replace(":", "").replace("-", "").strip().lower()
    if len(normalized) != expected_bytes * 2 or not HEX_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be {expected_bytes} bytes / {expected_bytes * 2} hex characters")
    return normalized


def generate_companion_keypair() -> tuple[str, str]:
    """Generate a NIST P-256 keypair; public key is the 64-byte X||Y point."""
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1())
    numbers = private_key.private_numbers()
    private_hex = numbers.private_value.to_bytes(32, "big").hex()
    public = numbers.public_numbers
    public_hex = (
        public.x.to_bytes(32, "big") + public.y.to_bytes(32, "big")
    ).hex()
    return private_hex, public_hex


def generate_pairing_keys(display_name: str) -> dict[str, Any]:
    private_hex, public_hex = generate_companion_keypair()
    return {
        "created_at": utc_now(),
        "display_name": display_name,
        "companion_id": secrets.token_hex(16),
        "companion_private_key": private_hex,
        "companion_public_key": public_hex,
    }


def load_or_create_pairing_keys(
    path: Path,
    display_name: str,
    companion_id: str | None,
    companion_public_key: str | None,
    reset_key: bool,
) -> dict[str, Any]:
    if path.exists() and not reset_key:
        keys = json.loads(path.read_text(encoding="utf-8"))
        if not keys.get("companion_private_key") and not companion_public_key:
            # Key files written before keypairs were real EC points get a
            # valid keypair generated in place, keeping the companion id.
            private_hex, public_hex = generate_companion_keypair()
            keys["companion_private_key"] = private_hex
            keys["companion_public_key"] = public_hex
    else:
        keys = generate_pairing_keys(display_name)

    if companion_id:
        keys["companion_id"] = companion_id
    if companion_public_key:
        keys["companion_public_key"] = companion_public_key
    keys["display_name"] = keys.get("display_name") or display_name
    keys["companion_id"] = normalize_hex(keys["companion_id"], 16, "companion id")
    keys["companion_public_key"] = normalize_hex(keys["companion_public_key"], 64, "companion public key")
    if keys.get("companion_private_key"):
        keys["companion_private_key"] = normalize_hex(
            keys["companion_private_key"], 32, "companion private key"
        )

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(keys, indent=2))
    path.chmod(0o600)
    return keys


def write_json_atomic(path: Path, payload: dict[str, Any], mode: int | None = None) -> None:
    write_private_json_atomic(path, payload, mode=mode or 0o600)


def make_event(sender: Any, data: bytes | bytearray, source: str) -> dict[str, Any]:
    raw = bytes(data)
    hex_value = bytes_to_hex(raw)
    return {
        "received_at": utc_now(),
        "source": source,
        "sender": str(sender),
        "length": len(raw),
        "hex": hex_value,
        "decoded": decode_hex_frame(hex_value),
    }


def extract_pairing_response(event: dict[str, Any]) -> dict[str, Any] | None:
    decoded = event.get("decoded") or {}
    envelope = decoded.get("envelope") or {}
    candidate = envelope.get("body_plain_candidate") or {}
    parsed = candidate.get("parsed_payload")
    if parsed and parsed.get("kind") == "pairing_response":
        response = dict(parsed)
        response["transport_sequence"] = decoded.get("sequence")
        response["message_version"] = candidate.get("message_version")
        response["source"] = event.get("source")
        response["received_at"] = event.get("received_at")
        return response
    return None


def build_pairing_frame(
    sequence: int,
    version: int,
    companion_id: str,
    companion_public_key: str,
    display_name: str,
) -> bytes:
    body = build_pairing_body(companion_id, companion_public_key, display_name)
    return build_command_frame(sequence, version, 0x0A, body)


def build_pairing_summary(
    address: str,
    keys: dict[str, Any],
    pairing_response: dict[str, Any],
    hub_name: str,
    hub_serial: str | None,
    hub_model: str,
    hub_software_revision: str | None,
    hub_wifi_mac: str | None,
) -> dict[str, Any]:
    companion_id = normalize_hex(keys["companion_id"], 16, "companion id")
    companion_public_key = normalize_hex(keys["companion_public_key"], 64, "companion public key")
    return {
        "paired_at": utc_now(),
        "companion_id": companion_id,
        "companion_records": [
            {
                "companion_id": companion_id,
                "companion_public_key": companion_public_key,
                "display_name": keys.get("display_name"),
            }
        ],
        "hub": {
            "display_name": hub_name,
            "serial_number": hub_serial,
            "model": hub_model,
            "software_revision": hub_software_revision,
            "wifi_mac": hub_wifi_mac,
            "ble_address": address,
            "appliance_id": pairing_response.get("appliance_id"),
            "appliance_public_key": pairing_response.get("appliance_public_key"),
        },
        "pairing_response": pairing_response,
    }


async def pair_once(args: argparse.Namespace, keys: dict[str, Any]) -> dict[str, Any]:
    try:
        from bleak import BleakClient
    except ImportError as exc:
        raise RuntimeError("bleak is not installed; run pip install -r requirements.txt") from exc

    events: list[dict[str, Any]] = []
    pairing_responses: list[dict[str, Any]] = []
    pairing_seen = asyncio.Event()

    first_reply = asyncio.Event()
    hub_version: int | None = None
    version_rejected = asyncio.Event()
    version_error_count = 0
    pairing_required = asyncio.Event()
    handshake_success = asyncio.Event()

    def process_event(event: dict[str, Any]) -> None:
        nonlocal hub_version, version_error_count
        events.append(event)
        first_reply.set()
        decoded = event.get("decoded") or {}
        envelope = decoded.get("envelope") or {}
        candidate = envelope.get("body_plain_candidate") or {}
        LOGGER.info(
            "Hub reply source=%s type=%s length=%s",
            event.get("source"),
            candidate.get("type_name") or "UNDECODED",
            event.get("length"),
        )
        LOGGER.debug("Hub reply frame: %s", event.get("hex"))
        parsed = candidate.get("parsed_payload") or {}
        if parsed.get("kind") == "error" and parsed.get("error_type") == "UNSUPPORTED_MESSAGE_VERSION":
            hub_version = candidate.get("message_version")
            version_error_count += 1
            version_rejected.set()
        if candidate.get("type_value") == 0xF1:
            LOGGER.info("Hub requests pairing")
            pairing_required.set()
        if candidate.get("type_value") == 0xF2:
            LOGGER.info("Hub accepted the handshake")
            handshake_success.set()
        response = extract_pairing_response(event)
        if response is not None:
            pairing_responses.append(response)
            LOGGER.info("Pairing response: %s", response.get("status"))
            pairing_seen.set()

    def handler(source: str):
        def on_notify(sender: Any, data: bytearray) -> None:
            process_event(make_event(sender, data, source))

        return on_notify

    def build_frames(version: int, base_sequence: int) -> tuple[bytes, bytes]:
        handshake = build_command_frame(
            sequence=base_sequence,
            message_version=version,
            type_value=0x70,
            payload=build_handshake_body(keys["companion_id"], secrets.token_bytes(32)),
        )
        pairing = build_pairing_frame(
            sequence=base_sequence + 1,
            version=version,
            companion_id=keys["companion_id"],
            companion_public_key=keys["companion_public_key"],
            display_name=keys.get("display_name") or args.display_name,
        )
        return handshake, pairing

    async with BleakClient(args.address, timeout=args.timeout) as client:
        async def write_frame(data: bytes) -> None:
            # The hub treats every ATT write as one complete frame, so frames
            # must never be fragmented (HCI capture of the official app shows
            # single large writes over a negotiated MTU).
            await client.write_gatt_char(
                COMMAND_UUID, data, response=not args.write_without_response
            )

        subscribed: list[str] = []
        for source, uuid in (
            ("response", RESPONSE_UUID),
            ("status", STATUS_UUID),
            ("notification", NOTIFICATION_UUID),
        ):
            try:
                await client.start_notify(uuid, handler(source))
                subscribed.append(uuid)
            except Exception as exc:
                LOGGER.warning("Could not subscribe %s: %r", source, exc)

        # The hub parks replies in the response characteristic without sending
        # a notification, so it must be polled.
        last_response_hex: str | None = None

        async def poll_response(duration: float, stop, interval: float = 1.0) -> None:
            nonlocal last_response_hex
            deadline = asyncio.get_running_loop().time() + duration
            while asyncio.get_running_loop().time() < deadline:
                if stop():
                    return
                try:
                    value = bytes(await client.read_gatt_char(RESPONSE_UUID))
                except Exception as exc:
                    LOGGER.debug("Could not read response characteristic: %r", exc)
                    value = b""
                if value:
                    event = make_event(RESPONSE_UUID, value, "response-poll")
                    if event["hex"] != last_response_hex:
                        last_response_hex = event["hex"]
                        process_event(event)
                if stop():
                    return
                await asyncio.sleep(interval)

        # The official app claims the session slot by writing 0x01 to the
        # session characteristic before greeting; without it the hub leaves
        # commands unprocessed.
        try:
            await client.write_gatt_char(SESSION_UUID, b"\x01", response=True)
            LOGGER.info("Claimed session slot on %s", SESSION_UUID)
        except Exception as exc:
            LOGGER.warning("Could not claim session slot: %r", exc)

        # Sequential conversation: greet, let the hub answer, then pair at
        # the version the hub accepts. The hub answers a valid greeting from
        # an unknown companion with INCOMING_PAIRING_REQUIRED (0xF1).
        version = args.version
        sequence = 1
        for _ in range(3):
            errors_before = version_error_count
            version_rejected.clear()
            handshake_frame, _ = build_frames(version, sequence)
            sequence += 2
            LOGGER.info("Greeting hub at message version %s", version)
            await write_frame(handshake_frame)
            await poll_response(
                10.0,
                stop=lambda before=errors_before: (
                    pairing_required.is_set()
                    or handshake_success.is_set()
                    or version_error_count > before
                ),
            )
            if pairing_required.is_set() or handshake_success.is_set():
                break
            if version_error_count > errors_before and hub_version and hub_version != version:
                LOGGER.info("Hub rejected version %s; switching to its version %s", version, hub_version)
                version = hub_version
                continue
            break

        args.version = version
        _, frame = build_frames(version, sequence)
        LOGGER.info(
            "Sending pairing request at message version %s; waiting %ss for the response "
            "(confirm on the hub if it prompts)",
            version,
            args.listen_seconds,
        )
        await write_frame(frame)
        await poll_response(args.listen_seconds, stop=pairing_seen.is_set)
        if not pairing_seen.is_set():
            LOGGER.warning("No pairing response received within %ss", args.listen_seconds)

        for uuid in subscribed:
            try:
                await client.stop_notify(uuid)
            except Exception:
                pass

        latest_response = pairing_responses[-1] if pairing_responses else None
        result = {
            "paired_at": utc_now(),
            "address": args.address,
            "connected": client.is_connected,
            "message_version": args.version,
            "write_without_response": args.write_without_response,
            "display_name": keys.get("display_name"),
            "companion_id": keys["companion_id"],
            "companion_public_key": keys["companion_public_key"],
            "pairing_response": latest_response,
            "pairing_responses": pairing_responses,
            "events": events,
        }
        return result


async def async_main(args: argparse.Namespace) -> int:
    keys = load_or_create_pairing_keys(
        path=args.pairing_key_file,
        display_name=args.display_name,
        companion_id=args.companion_id,
        companion_public_key=args.companion_public_key,
        reset_key=args.reset_key,
    )
    LOGGER.info("Using pairing key material from %s", args.pairing_key_file)
    LOGGER.info("Wake the Weber hub and confirm pairing if prompted.")

    result = await pair_once(args, keys)
    write_json_atomic(args.json_out, result, mode=0o600)

    pairing_response = result.get("pairing_response")
    if not pairing_response:
        LOGGER.error("No pairing response received; wrote %s", args.json_out)
        return 2

    status = pairing_response.get("status")
    if status != "CONFIRMED":
        LOGGER.error("Pairing was not confirmed: %s; wrote %s", status, args.json_out)
        return 3

    summary = build_pairing_summary(
        address=args.address,
        keys=keys,
        pairing_response=pairing_response,
        hub_name=args.hub_name,
        hub_serial=args.hub_serial,
        hub_model=args.hub_model,
        hub_software_revision=args.hub_software_revision,
        hub_wifi_mac=args.hub_wifi_mac,
    )
    write_json_atomic(args.pairing_summary_out, summary, mode=0o600)
    LOGGER.info("Pairing confirmed; wrote %s", args.pairing_summary_out)
    return 0


async def pair_until_stopped(args: argparse.Namespace) -> int:
    """Run pairing and disconnect from the hub cleanly on SIGTERM/SIGINT."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    stop_requested = False

    def request_stop(signum: int) -> None:
        nonlocal stop_requested
        stop_requested = True
        LOGGER.info(
            "Received %s; disconnecting from hub and shutting down",
            signal.Signals(signum).name,
        )
        task.cancel()

    registered: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop, sig)
        except (NotImplementedError, RuntimeError):
            continue
        registered.append(sig)

    try:
        return await async_main(args)
    except asyncio.CancelledError:
        if stop_requested:
            return 0
        raise
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pair Weber Connect Hub for local BLE telemetry.")
    parser.add_argument("--address", required=True, help="BLE address. Linux/Home Assistant uses a MAC address.")
    parser.add_argument("--pairing-key-file", type=Path, default=DEFAULT_KEY_FILE)
    parser.add_argument("--pairing-summary-out", type=Path, default=DEFAULT_SUMMARY_OUT)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_RESULT_OUT)
    parser.add_argument("--companion-id", default=None)
    parser.add_argument("--companion-public-key", default=None)
    parser.add_argument("--display-name", default="Home Assistant")
    parser.add_argument("--hub-name", default="Weber Connect Hub")
    parser.add_argument("--hub-serial", default=None)
    parser.add_argument("--hub-model", default="Connect Hub")
    parser.add_argument("--hub-software-revision", default=None)
    parser.add_argument("--hub-wifi-mac", default=None)
    parser.add_argument("--version", type=int, default=11)
    parser.add_argument("--listen-seconds", type=float, default=60.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--write-without-response", action="store_true")
    parser.add_argument("--reset-key", action="store_true")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(pair_until_stopped(args))


if __name__ == "__main__":
    raise SystemExit(main())

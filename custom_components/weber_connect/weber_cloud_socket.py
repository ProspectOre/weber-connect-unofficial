"""Weber companion WebSocket protocol for live cook sessions and controls.

The official companion API routes the same appliance messages used over BLE
through a binary WebSocket envelope.  This module intentionally implements a
small, auditable subset: status/program reads and commands for an already
active cook.  It never installs a recipe or configures appliance networking.
"""

from __future__ import annotations

import logging
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .saber_frames import parse_cook_session_status_payload

LOGGER = logging.getLogger("weber_connect_cloud_socket")

SOCKET_PATH = "/2/messaging/websocket/companion"
ROUTING_HEADER_LENGTH = 35
TRANSPORT_HEADER_LENGTH = 6
MESSAGE_VERSION = 10

SESSION_TYPES = {0: "UNKNOWN", 1: "PROBED", 2: "TIMED", 3: "TIMER"}
COOK_MODES = {
    0: "unknown",
    1: "grill",
    2: "smoke_boost",
    3: "preheat",
    4: "indirect",
    5: "custom",
    6: "simple",
    7: "manual",
    8: "sear",
    9: "steam",
    10: "warm",
}
TRIGGER_TYPES = {
    0: "unknown",
    1: "duration",
    2: "cavity_temp_ceiling",
    3: "probe_temp_ceiling",
    4: "food_present",
    5: "user_interaction",
    6: "probe_connected",
    7: "remaining",
    8: "cavity_temp_floor",
    9: "probe_temp_floor",
    10: "door_event",
    11: "at_temp",
    224: "eta_duration",
    225: "eta_probe_temp_ceiling",
    226: "eta_remaining",
}


class WeberCloudSocketError(RuntimeError):
    """The companion WebSocket rejected or returned an invalid message."""


@dataclass(frozen=True, slots=True)
class RoutedMessage:
    source_id: str
    target_id: str
    sequence: int
    message_version: int
    type_value: int
    payload: bytes


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, length: int) -> bytes:
        if length < 0 or self.offset + length > len(self.data):
            raise WeberCloudSocketError("Cook-program payload ended unexpectedly.")
        value = self.data[self.offset : self.offset + length]
        self.offset += length
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int(struct.unpack("<H", self.take(2))[0])

    def i16(self) -> int:
        return int(struct.unpack("<h", self.take(2))[0])

    def i32(self) -> int:
        return int(struct.unpack("<i", self.take(4))[0])

    def u32(self) -> int:
        return int(struct.unpack("<I", self.take(4))[0])

    def josl_string(self) -> str:
        length = self.u8()
        try:
            return self.take(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WeberCloudSocketError("Cook-program text is not valid UTF-8.") from exc


def decode_routed_message(data: bytes) -> RoutedMessage:
    """Decode Weber's routing + transport + appliance headers."""

    minimum = ROUTING_HEADER_LENGTH + TRANSPORT_HEADER_LENGTH + 2
    if len(data) < minimum:
        raise WeberCloudSocketError("Cloud socket message is too short.")
    if data[0] != 1:
        raise WeberCloudSocketError(f"Unsupported routing version {data[0]}.")
    if data[1] not in {1, 2} or data[18] not in {1, 2}:
        raise WeberCloudSocketError("Cloud socket routing header is invalid.")
    source_id = data[2:18].hex()
    target_id = data[19:35].hex()
    sequence, length = struct.unpack_from("<IH", data, ROUTING_HEADER_LENGTH)
    body = data[ROUTING_HEADER_LENGTH + TRANSPORT_HEADER_LENGTH :]
    if len(body) != length:
        raise WeberCloudSocketError(
            f"Cloud socket transport length mismatch ({length} != {len(body)})."
        )
    return RoutedMessage(
        source_id=source_id,
        target_id=target_id,
        sequence=sequence,
        message_version=body[0],
        type_value=body[1],
        payload=body[2:],
    )


def encode_routed_message(
    companion_id: str,
    appliance_id: str,
    sequence: int,
    type_value: int,
    payload: bytes = b"",
    *,
    message_version: int = MESSAGE_VERSION,
) -> bytes:
    """Build the official v2 targeted-companion WebSocket envelope."""

    try:
        source = bytes.fromhex(companion_id)
        target = bytes.fromhex(appliance_id)
    except ValueError as exc:
        raise ValueError("Companion and appliance IDs must be hexadecimal.") from exc
    if len(source) != 16 or len(target) != 16:
        raise ValueError("Companion and appliance IDs must each be 16 bytes.")
    appliance = bytes([message_version & 0xFF, type_value & 0xFF]) + payload
    routing = bytes([1, 2]) + source + bytes([2]) + target
    transport = struct.pack("<IH", sequence & 0xFFFFFFFF, len(appliance))
    return routing + transport + appliance


def _trigger(reader: _Reader) -> dict[str, Any]:
    target = reader.i32()
    type_value = reader.u8()
    if type_value in {2, 3, 8, 9, 225}:
        target *= 100
    return {
        "type_value": type_value,
        "type": TRIGGER_TYPES.get(type_value, "unknown"),
        "target": target,
    }


def decode_program_details(payload: bytes, message_version: int) -> dict[str, Any]:
    """Decode INCOMING_PROGRAM_DETAILS into HA-safe recipe/session fields."""

    reader = _Reader(payload)
    session_type_value = reader.u8()
    session_index = reader.u8()
    program_id = str(uuid.UUID(bytes=reader.take(16)))
    plan_id = reader.u32() if message_version >= 11 else reader.u8()
    title = reader.josl_string()
    eta_curve = reader.u8()
    step_count = reader.u16() if message_version >= 11 else reader.u8()
    if step_count > 256:
        raise WeberCloudSocketError("Cook program contains too many steps.")
    steps: list[dict[str, Any]] = []
    for _ in range(step_count):
        step_id = reader.u16() if message_version >= 11 else reader.u8()
        duration_ms = reader.u32()
        temperature_dc = reader.i16()
        cook_mode_value = reader.u8() if message_version >= 10 else None
        criteria_count = reader.u8()
        criteria = [_trigger(reader) for _ in range(criteria_count)]
        requirement_value = reader.u8()
        steps.append(
            {
                "id": step_id,
                "base_duration_s": round(duration_ms / 1000),
                "target_temperature_c": (
                    None if temperature_dc == -32768 else round(temperature_dc / 10, 1)
                ),
                "cook_mode": (
                    COOK_MODES.get(cook_mode_value, "unknown")
                    if cook_mode_value is not None
                    else "unknown"
                ),
                "cook_mode_value": cook_mode_value,
                "exit_criteria": criteria,
                "requirement": "all" if requirement_value == 1 else "any",
            }
        )
    prompt_count = reader.u16() if message_version >= 11 else reader.u8()
    if prompt_count > 1024:
        raise WeberCloudSocketError("Cook program contains too many prompts.")
    prompts: list[dict[str, Any]] = []
    for _ in range(prompt_count):
        prompt_id = reader.u16() if message_version >= 11 else reader.u8()
        step_id = reader.u16() if message_version >= 11 else reader.u8()
        prompts.append(
            {
                "id": prompt_id,
                "step_id": step_id,
                "trigger": _trigger(reader),
                "short_title": reader.josl_string(),
                "instruction": reader.josl_string(),
            }
        )
    return {
        "title": title,
        "program_id": program_id,
        "program_id_hex": program_id.replace("-", ""),
        "plan_id": plan_id,
        "session_type": SESSION_TYPES.get(session_type_value, "UNKNOWN"),
        "session_type_value": session_type_value,
        "session_index": session_index,
        "eta_curve": eta_curve,
        "steps": steps,
        "prompts": prompts,
        "unparsed_bytes": len(payload) - reader.offset,
    }


def active_cook_from_program(status: dict[str, Any], program: dict[str, Any]) -> dict[str, Any]:
    """Select the active step and prompt referenced by a status message."""

    matching_probe = next(
        (
            probe
            for probe in status.get("probes", [])
            if str(probe.get("program_id_hex") or "").replace(":", "").lower()
            == program.get("program_id_hex")
            and probe.get("plan_id") == program.get("plan_id")
        ),
        None,
    )
    if not matching_probe:
        return {**program, "active": False}
    step_id = matching_probe.get("step_id")
    prompt_id = matching_probe.get("prompt_id")
    step = next((row for row in program["steps"] if row["id"] == step_id), None)
    prompt = next(
        (row for row in program["prompts"] if row["id"] == prompt_id and row["step_id"] == step_id),
        None,
    )
    return {
        **program,
        "active": True,
        "probe_number": matching_probe.get("probe_number"),
        "state": matching_probe.get("state"),
        "step_id": step_id,
        "prompt_id": prompt_id,
        "current_step": step,
        "current_prompt": prompt,
        "current_instruction": (prompt or {}).get("instruction"),
        "time_remaining_s": matching_probe.get("time_remaining_s"),
        "time_elapsed_s": matching_probe.get("time_elapsed_s"),
        "prompt_time_remaining_s": matching_probe.get("prompt_time_remaining_s"),
    }


class WeberCloudSocketClient:
    """Persistent synchronous client; callers invoke it in a worker thread."""

    def __init__(
        self,
        cloud_client: Any,
        *,
        timeout: float = 8.0,
        subscribe_delay: float = 0.05,
    ) -> None:
        self.cloud_client = cloud_client
        self.timeout = timeout
        self.subscribe_delay = subscribe_delay
        self._connection: Any = None
        self._sequence = 1
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                LOGGER.debug("Could not close cloud socket cleanly", exc_info=True)

    def _connect(self) -> Any:
        if self._connection is not None:
            return self._connection
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise WeberCloudSocketError("The websockets runtime dependency is missing.") from exc
        self._connection = connect(
            f"wss://{self.cloud_client.messaging_host}{SOCKET_PATH}",
            additional_headers={"Authorization": f"Bearer {self.cloud_client.token()}"},
            user_agent_header=self.cloud_client.user_agent,
            open_timeout=self.timeout,
            ping_interval=40,
            ping_timeout=20,
            close_timeout=3,
            compression=None,
            max_size=1024 * 1024,
            proxy=None,
        )
        return self._connection

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence = 1 if value >= 0xFFFFFFFF else value + 1
        return value

    def _send(self, appliance_id: str, type_value: int, payload: bytes = b"") -> int:
        sequence = self._next_sequence()
        self._connect().send(
            encode_routed_message(
                self.cloud_client.config.device_id,
                appliance_id,
                sequence,
                type_value,
                payload,
            )
        )
        return sequence

    def _receive_until(self, accepted_types: set[int]) -> RoutedMessage:
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Weber cloud did not answer the appliance request.")
            try:
                raw = self._connect().recv(timeout=remaining)
            except Exception:
                self._close_unlocked()
                raise
            if not isinstance(raw, bytes):
                continue
            message = decode_routed_message(raw)
            if message.type_value == 0x87:
                raise WeberCloudSocketError("The hub rejected the cloud command.")
            if message.type_value in accepted_types:
                return message

    def _subscribe(self, appliance_id: str) -> None:
        """Send the request sequence used by the official companion client."""

        now = int(time.time())

        def timestamp(value: int) -> bytes:
            return bytes([0x15, 0x04]) + struct.pack("<I", value)

        requests = (
            (0x0E, b""),
            (0x05, b""),
            (0x09, timestamp(now)),
            (0x07, b""),
            (0x0B, b"\x01\x00"),
            (0x0E, b""),
            (0x09, timestamp(now + 1)),
            (0x05, b""),
            (0x07, b""),
        )
        for type_value, payload in requests:
            self._send(appliance_id, type_value, payload)
            if self.subscribe_delay:
                time.sleep(self.subscribe_delay)

    def live_status(self, appliance_id: str) -> dict[str, Any]:
        """Fetch live status and the installed program for each active probe."""

        with self._lock:
            self._subscribe(appliance_id)
            response = self._receive_until({0x80})
            status = parse_cook_session_status_payload(response.payload)
            programs: list[dict[str, Any]] = []
            requested: set[tuple[int, int]] = set()
            for probe in status.get("probes", []):
                slot = probe.get("slot_index")
                program_id = probe.get("program_id_hex")
                if not isinstance(slot, int) or not program_id:
                    continue
                key = (1, slot)
                if key in requested:
                    continue
                requested.add(key)
                self._send(appliance_id, 0x0B, bytes(key))
                details_message = self._receive_until({0x86})
                details = decode_program_details(
                    details_message.payload,
                    details_message.message_version,
                )
                programs.append(active_cook_from_program(status, details))
            status["programs"] = programs
            status["active_cook"] = next(
                (program for program in programs if program.get("active")), None
            )
            return status

    def session_command(
        self,
        appliance_id: str,
        active_cook: dict[str, Any],
        command: str,
    ) -> None:
        command_values = {"stop": 3, "remove": 4, "confirm": 7}
        if command not in command_values:
            raise ValueError("Unsupported cook-session command.")
        try:
            program_id = uuid.UUID(str(active_cook["program_id"])).bytes
            plan_id = int(active_cook["plan_id"])
            session_type = int(active_cook["session_type_value"])
            session_index = int(active_cook["session_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WeberCloudSocketError("No controllable active cook is available.") from exc
        payload = (
            bytes([1, 1, command_values[command], 2, 1, session_type, 3, 1, session_index])
            + bytes([4, 16])
            + program_id
            + bytes([5, 4])
            + struct.pack("<I", plan_id)
        )
        current_step = active_cook.get("step_id")
        if isinstance(current_step, int):
            payload += bytes([6, 2]) + struct.pack("<H", current_step)
        with self._lock:
            self._send(appliance_id, 0x01, payload)

    def timer_command(
        self, appliance_id: str, timer_index: int, action: str, duration_s: int = 0
    ) -> None:
        if timer_index < 0 or timer_index > 3:
            raise ValueError("Timer index must be between 0 and 3.")
        if action not in {"start", "reset"}:
            raise ValueError("Timer action must be start or reset.")
        if action == "start" and not 1 <= duration_s <= 86_400:
            raise ValueError("Timer duration must be between 1 second and 24 hours.")
        payload = bytes([timer_index, 1 if action == "start" else 2]) + struct.pack(
            "<I", duration_s * 1000 if action == "start" else 0
        )
        with self._lock:
            self._send(appliance_id, 0x02, payload)

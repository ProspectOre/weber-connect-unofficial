#!/usr/bin/env python3
"""Helpers for Weber/June Saber BLE frames.

This implements the observable transport and "null session" wrapper used by
the Weber Connect Android app. It does not implement the JOSL secure-session
decryptor; encrypted response bodies are identified and left as ciphertext.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

APPLIANCE_SERVICE_UUID = "01014a75-6e65-81de-a4b2-940b4c6f6b69"
STATUS_UUID = "31014a75-6e65-81de-a4b2-940b4c6f6b69"
NOTIFICATION_UUID = "31024a75-6e65-81de-a4b2-940b4c6f6b69"
COMMAND_UUID = "31034a75-6e65-81de-a4b2-940b4c6f6b69"
SESSION_UUID = "31044a75-6e65-81de-a4b2-940b4c6f6b69"
RESPONSE_UUID = "31084a75-6e65-81de-a4b2-940b4c6f6b69"

DEFAULT_MESSAGE_VERSION = 11

OUTGOING_TYPES = {
    0x01: "OUTGOING_SESSION_COMMAND",
    0x02: "OUTGOING_TIMER_COMMAND",
    0x04: "OUTGOING_PLAN_PAYLOAD",
    0x05: "OUTGOING_FETCH_STATUS",
    0x06: "OUTGOING_FETCH_SESSION_DETAILS",
    0x07: "OUTGOING_FETCH_APPLIANCE_STATUS",
    0x08: "OUTGOING_PROXY_RESPONSE",
    0x09: "OUTGOING_SET_DEVICE_SETTINGS",
    0x0A: "OUTGOING_PAIRING_REQUEST",
    0x0B: "OUTGOING_FETCH_PROGRAM_DETAILS",
    0x0C: "OUTGOING_SET_COOK_MODE",
    0x0D: "OUTGOING_CONFIGURE_WIFI",
    0x0E: "OUTGOING_FETCH_APPLIANCE_CAPABILITIES",
    0x0F: "OUTGOING_SCAN_WIFI_NETWORKS",
    0x10: "OUTGOING_APPLIANCE_COMMAND",
    0x12: "OUTGOING_SET_VALVE_INTENSITIES",
    0x13: "OUTGOING_SET_AUXILIARY_BURNER_BEHAVIOR",
    0x14: "OUTGOING_CANCEL_IGNITION_REQUEST",
    0x15: "OUTGOING_PROVISIONING_RECORD",
    0x18: "OUTGOING_PROBE_LINKING_REQUEST",
    0x19: "OUTGOING_FETCH_LINKED_PROBES",
    0x70: "OUTGOING_HANDSHAKE_GREETING",
}

INCOMING_TYPES = {
    0x80: "INCOMING_STATUS",
    0x81: "INCOMING_NOTIFICATION",
    0x82: "INCOMING_SESSION_DETAILS",
    0x83: "INCOMING_APPLIANCE_STATUS",
    0x84: "INCOMING_PROXY_REQUEST",
    0x85: "INCOMING_PAIRING_RESPONSE",
    0x86: "INCOMING_PROGRAM_DETAILS",
    0x87: "INCOMING_ERROR_MESSAGE",
    0x88: "INCOMING_APPLIANCE_CAPABILITIES",
    0x89: "INCOMING_WIFI_SCAN_RESULTS",
    0x8A: "INCOMING_PROVISIONING_RESPONSE",
    0x8C: "INCOMING_PROBE_LINKING_RESPONSE",
    0x8D: "INCOMING_LINKED_PROBES_RESPONSE",
    0xF0: "INCOMING_HANDSHAKE_REQUIRED",
    0xF1: "INCOMING_PAIRING_REQUIRED",
    0xF2: "INCOMING_HANDSHAKE_SUCCESS",
}

PAIRING_RESPONSE_STATUS = {
    0x00: "CONFIRMED",
    0x01: "REJECTED",
    0x02: "TIMED_OUT",
}

ERROR_TYPES = {
    0x00: "UNSUPPORTED_MESSAGE_VERSION",
    0x01: "INVALID_APPLIANCE_STATE",
    0xFF: "UNKNOWN",
}


@dataclass(frozen=True)
class AppliancePayload:
    message_version: int
    type_value: int
    type_name: str
    payload_hex: str
    payload_length: int
    parsed_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class EncryptionEnvelope:
    header_byte: int
    message_count: int
    verification_code: int
    message_type: int
    body_length: int
    body_hex: str
    footer_crc8: int
    calculated_crc8: int
    crc_ok: bool
    tail_byte: int
    body_plain_candidate: AppliancePayload | None
    extra_hex: str


@dataclass(frozen=True)
class TransportFrame:
    sequence: int
    length: int
    length_ok: bool
    envelope: EncryptionEnvelope | None
    payload_hex: str
    extra_hex: str


def bytes_to_hex(data: bytes) -> str:
    return data.hex(":")


def hex_to_bytes(text: str) -> bytes:
    cleaned = (
        text.replace(":", "")
        .replace(" ", "")
        .replace("\n", "")
        .replace("\t", "")
        .removeprefix("0x")
    )
    if len(cleaned) % 2:
        raise ValueError("hex input must have an even number of digits")
    return bytes.fromhex(cleaned)


def crc8(data: bytes, initial: int = 0) -> int:
    """JOSL CRC-8 used in the Saber envelope footer.

    The native library uses a reflected bit loop with polynomial constant
    0x8c, equivalent to CRC-8/MAXIM-DOW with an initial value of zero.
    """

    crc = initial & 0xFF
    for value in data:
        byte = value
        for _ in range(8):
            mix = (crc ^ byte) & 0x01
            crc >>= 1
            if mix:
                crc ^= 0x8C
            byte >>= 1
        crc &= 0xFF
    return crc


def type_name(type_value: int) -> str:
    return INCOMING_TYPES.get(type_value) or OUTGOING_TYPES.get(type_value) or "UNKNOWN"


def build_appliance_payload(
    message_version: int,
    type_value: int,
    payload: bytes = b"",
) -> bytes:
    return bytes([message_version & 0xFF, type_value & 0xFF]) + payload


def build_handshake_body(companion_id_hex: str, nonce: bytes) -> bytes:
    companion_id = hex_to_bytes(companion_id_hex)
    if len(companion_id) != 16:
        raise ValueError("companion id must be 16 bytes / 32 hex characters")
    if len(nonce) != 32:
        raise ValueError("nonce must be 32 bytes")
    return companion_id + nonce


def build_josl_string(text: str, max_byte_length: int = 32) -> bytes:
    """Build the app's one-byte-length UTF-8 string field."""

    value = text or ""
    while len(value.encode("utf-8")) > max_byte_length:
        value = value[1:]
    encoded = value.encode("utf-8")
    return bytes([len(encoded)]) + encoded


def build_pairing_body(
    companion_id_hex: str,
    companion_public_key_hex: str,
    display_name: str,
) -> bytes:
    companion_id = hex_to_bytes(companion_id_hex)
    companion_public_key = hex_to_bytes(companion_public_key_hex)
    if len(companion_id) != 16:
        raise ValueError("companion id must be 16 bytes / 32 hex characters")
    if len(companion_public_key) != 64:
        raise ValueError("companion public key must be 64 bytes / 128 hex characters")
    return companion_id + companion_public_key + build_josl_string(display_name)


def wrap_null_session(appliance_payload: bytes, message_type: int = 0) -> bytes:
    """Wrap a plaintext appliance payload with the app's null-session envelope."""

    body_length = len(appliance_payload)
    header = bytes([0xAB, 0x00, 0x00, message_type & 0xFF]) + body_length.to_bytes(
        2,
        "little",
    )
    crc = crc8(header[1:] + appliance_payload)
    return header + appliance_payload + bytes([crc, 0x54])


def build_transport_frame(sequence: int, wrapped_payload: bytes) -> bytes:
    return (
        int(sequence).to_bytes(4, "little", signed=False)
        + len(wrapped_payload).to_bytes(2, "little")
        + wrapped_payload
    )


def build_command_frame(
    sequence: int,
    message_version: int,
    type_value: int,
    payload: bytes = b"",
    message_type: int = 0,
) -> bytes:
    appliance_payload = build_appliance_payload(message_version, type_value, payload)
    return build_transport_frame(sequence, wrap_null_session(appliance_payload, message_type))


def parse_appliance_payload(body: bytes) -> AppliancePayload | None:
    if len(body) < 2:
        return None
    type_value = body[1]
    payload = body[2:]
    parsed_payload = parse_known_payload(type_value, payload)
    return AppliancePayload(
        message_version=body[0],
        type_value=type_value,
        type_name=type_name(type_value),
        payload_hex=bytes_to_hex(payload),
        payload_length=len(payload),
        parsed_payload=parsed_payload,
    )


def parse_known_payload(type_value: int, payload: bytes) -> dict[str, Any] | None:
    if type_value == 0x80:
        return parse_cook_session_status_payload(payload)
    if type_value == 0x85 and len(payload) >= 81:
        status = payload[80]
        return {
            "kind": "pairing_response",
            "appliance_id": bytes_to_hex(payload[:16]),
            "appliance_public_key": bytes_to_hex(payload[16:80]),
            "status_value": status,
            "status": PAIRING_RESPONSE_STATUS.get(status, "UNKNOWN"),
            "extra_hex": bytes_to_hex(payload[81:]),
        }
    if type_value == 0x87:
        return parse_error_payload(payload)
    return None


SESSION_STATES = {
    0: "UNKNOWN",
    1: "IDLE",
    2: "PROBED",
    3: "PRIMED",
    4: "READY",
    5: "ACTIVE",
    6: "PAUSED",
    7: "COMPLETE",
    8: "ERROR",
    9: "ACTIVE_FIXED",
    10: "PREHEAT",
}

PROBE_TYPES = {
    0: "UNKNOWN",
    1: "WIRED",
    2: "WIRELESS",
    3: "AMBIENT",
}


def parse_tlv(payload: bytes) -> dict[int, list[bytes]]:
    """Parse Weber's one-byte tag / one-byte length TLV records."""

    fields: dict[int, list[bytes]] = {}
    index = 0
    while index + 2 <= len(payload):
        tag = payload[index]
        length = payload[index + 1]
        start = index + 2
        end = start + length
        if end > len(payload):
            break
        fields.setdefault(tag, []).append(payload[start:end])
        index = end
    if index != len(payload):
        fields.setdefault(-1, []).append(payload[index:])
    return fields


def _lookup(table: dict[int, str], value: int | None) -> str:
    if value is None:
        return "UNKNOWN"
    return table.get(value, "UNKNOWN")


def _last(fields: dict[int, list[bytes]], tag: int) -> bytes | None:
    values = fields.get(tag)
    return values[-1] if values else None


def _u8(value: bytes | None) -> int | None:
    return value[0] if value else None


def _i16(value: bytes | None) -> int | None:
    if value is None or len(value) < 2:
        return None
    return int.from_bytes(value[:2], "little", signed=True)


def _u16(value: bytes | None) -> int | None:
    if value is None or len(value) < 2:
        return None
    return int.from_bytes(value[:2], "little", signed=False)


def _u32(value: bytes | None) -> int | None:
    if value is None or len(value) < 4:
        return None
    return int.from_bytes(value[:4], "little", signed=False)


def _u64(value: bytes | None) -> int | None:
    if value is None or len(value) < 8:
        return None
    return int.from_bytes(value[:8], "little", signed=False)


def _text(value: bytes | None) -> str | None:
    if not value:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _dc_temperature(value: int | None) -> dict[str, float | int] | None:
    """Convert deci-Celsius to Celsius/Fahrenheit."""

    if value is None or value == -32768:
        return None
    celsius = value / 10.0
    fahrenheit = celsius * 9.0 / 5.0 + 32.0
    return {
        "dc": value,
        "c": round(celsius, 1),
        "f": round(fahrenheit, 1),
    }


def _temperature_fields(value: int | None, prefix: str) -> dict[str, float | int | None]:
    converted = _dc_temperature(value)
    if converted is None:
        return {
            f"{prefix}_dc": value,
            f"{prefix}_c": None,
            f"{prefix}_f": None,
        }
    return {
        f"{prefix}_dc": converted["dc"],
        f"{prefix}_c": converted["c"],
        f"{prefix}_f": converted["f"],
    }


def parse_probe_session_status_tlv(payload: bytes) -> dict[str, Any]:
    """Parse a ProbeSessionStatusTLV nested inside INCOMING_STATUS."""

    fields = parse_tlv(payload)
    slot_index = _u8(_last(fields, 1))
    state_value = _u8(_last(fields, 12))
    probe_type_value = _u8(_last(fields, 19)) or _u8(_last(fields, 4))
    probe_temp_dc = _i16(_last(fields, 10))
    segment_temps = [_i16(item) for item in fields.get(23, [])]

    row: dict[str, Any] = {
        "slot_index": slot_index,
        "probe_number": slot_index + 1 if slot_index is not None else None,
        "label": f"Probe {slot_index + 1}" if slot_index is not None else "Probe",
        "session_id": _u8(_last(fields, 2)),
        "program_id_hex": bytes_to_hex(_last(fields, 3) or b"") or None,
        "plan_id": _u32(_last(fields, 16)) or _u8(_last(fields, 4)),
        "time_remaining_s": _u32(_last(fields, 5)),
        "time_elapsed_s": _u32(_last(fields, 6)),
        "step_id": _u16(_last(fields, 17)) or _u8(_last(fields, 7)),
        "prompt_time_remaining_s": _u32(_last(fields, 8)),
        "prompt_time_elapsed_s": _u32(_last(fields, 9)),
        "prompt_id": _u16(_last(fields, 18)) or _u8(_last(fields, 11)),
        "state_value": state_value,
        "state": _lookup(SESSION_STATES, state_value),
        "probe_type_value": probe_type_value,
        "probe_type": _lookup(PROBE_TYPES, probe_type_value),
        "serial_number": _text(_last(fields, 20)),
        "sku": _text(_last(fields, 21)),
        "battery_level": _u8(_last(fields, 22)),
        "active_events": [_u16(item) for item in fields.get(13, [])],
        "raw_tlv_hex": bytes_to_hex(payload),
    }
    row.update(_temperature_fields(probe_temp_dc, "probe_temp"))
    row.update(_temperature_fields(_i16(_last(fields, 24)), "case_temp"))
    row.update(_temperature_fields(_i16(_last(fields, 25)), "ambient_temp"))
    row["segment_temps"] = [_dc_temperature(value) for value in segment_temps if value is not None]
    if -1 in fields:
        row["unparsed_tail_hex"] = bytes_to_hex(fields[-1][-1])
    return row


def parse_cook_session_status_payload(payload: bytes) -> dict[str, Any]:
    """Parse the plaintext body of an INCOMING_STATUS message."""

    fields = parse_tlv(payload)
    probes = [parse_probe_session_status_tlv(item) for item in fields.get(4, [])]
    target_cavity_temp_dc = _i16(_last(fields, 1))
    display_cavity_temp_dc = _i16(_last(fields, 2))
    actual_cavity_temp_dc = _i16(_last(fields, 13))
    display_cavity_temp_f = _i16(_last(fields, 14))
    display_cavity_temp_c = _i16(_last(fields, 15))

    parsed: dict[str, Any] = {
        "kind": "cook_session_status",
        "probe_count": len(probes),
        "probes": probes,
        "cook_mode": _u8(_last(fields, 3)),
        "cook_history_session_id_hex": bytes_to_hex(_last(fields, 8) or b"") or None,
        "boot_count": _u32(_last(fields, 9)),
        "time_since_boot_raw": _u64(_last(fields, 10)),
        "simple_intensity": _u8(_last(fields, 11)),
        "cavity_temp_status": _u8(_last(fields, 12)),
    }
    parsed.update(_temperature_fields(target_cavity_temp_dc, "target_cavity_temp"))
    parsed.update(_temperature_fields(display_cavity_temp_dc, "display_cavity_temp"))
    parsed.update(_temperature_fields(actual_cavity_temp_dc, "actual_cavity_temp"))
    parsed["display_cavity_temp_f"] = display_cavity_temp_f
    parsed["display_cavity_temp_c"] = display_cavity_temp_c
    if -1 in fields:
        parsed["unparsed_tail_hex"] = bytes_to_hex(fields[-1][-1])
    return parsed


def parse_error_payload(payload: bytes) -> dict[str, Any]:
    fields: dict[int, list[bytes]] = {}
    index = 0
    while index + 2 <= len(payload):
        tag = payload[index]
        length = payload[index + 1]
        start = index + 2
        end = start + length
        if end > len(payload):
            break
        fields.setdefault(tag, []).append(payload[start:end])
        index = end

    error_type_value = None
    if fields.get(0):
        error_type_value = fields[0][-1][0] if fields[0][-1] else None
    software_version = None
    if fields.get(1):
        try:
            software_version = fields[1][-1].decode("utf-8")
        except UnicodeDecodeError:
            software_version = None

    return {
        "kind": "error",
        "error_type_value": error_type_value,
        "error_type": _lookup(ERROR_TYPES, error_type_value),
        "appliance_software_version": software_version,
        "unparsed_tail_hex": bytes_to_hex(payload[index:]),
    }


def parse_envelope(payload: bytes) -> EncryptionEnvelope | None:
    if len(payload) < 8:
        return None
    if payload[0] != 0xAB:
        return None
    body_length = int.from_bytes(payload[4:6], "little")
    body_start = 6
    body_end = body_start + body_length
    footer_end = body_end + 2
    if len(payload) < footer_end:
        return None

    body = payload[body_start:body_end]
    footer_crc = payload[body_end]
    calculated_crc = crc8(payload[1:body_end])
    plain_candidate = None
    if payload[0] == 0xAB and payload[1] == 0 and payload[2] == 0:
        plain_candidate = parse_appliance_payload(body)

    return EncryptionEnvelope(
        header_byte=payload[0],
        message_count=payload[1],
        verification_code=payload[2],
        message_type=payload[3],
        body_length=body_length,
        body_hex=bytes_to_hex(body),
        footer_crc8=footer_crc,
        calculated_crc8=calculated_crc,
        crc_ok=footer_crc == calculated_crc,
        tail_byte=payload[body_end + 1],
        body_plain_candidate=plain_candidate,
        extra_hex=bytes_to_hex(payload[footer_end:]),
    )


def parse_transport_frame(data: bytes) -> TransportFrame | None:
    if len(data) < 6:
        return None

    sequence = int.from_bytes(data[0:4], "little")
    length = int.from_bytes(data[4:6], "little")
    payload = data[6 : 6 + length]
    extra = data[6 + length :]
    return TransportFrame(
        sequence=sequence,
        length=length,
        length_ok=len(payload) == length,
        envelope=parse_envelope(payload),
        payload_hex=bytes_to_hex(payload),
        extra_hex=bytes_to_hex(extra),
    )


def decode_hex_frame(text: str) -> dict[str, Any]:
    data = hex_to_bytes(text)
    transport = parse_transport_frame(data)
    if transport is not None and transport.envelope is not None:
        return asdict(transport)
    envelope = parse_envelope(data)
    if envelope is not None:
        return {"envelope": asdict(envelope)}
    return {"raw_hex": bytes_to_hex(data), "length": len(data)}

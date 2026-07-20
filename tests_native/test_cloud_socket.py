from __future__ import annotations

import struct
import sys
import types
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from custom_components.weber_connect import weber_cloud_socket as socket

COMPANION_ID = "00112233445566778899aabbccddeeff"
APPLIANCE_ID = "ffeeddccbbaa99887766554433221100"
PROGRAM_ID = uuid.UUID("12345678-1234-5678-9abc-def012345678")


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag, len(value)]) + value


def josl(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return bytes([len(encoded)]) + encoded


def program_payload() -> bytes:
    # IncomingProgramDetailsMessage v11 followed by CookProgramPayload.
    header = bytes([1, 0]) + PROGRAM_ID.bytes + struct.pack("<I", 42)
    step = (
        struct.pack("<HIh", 7, 90_000, 635)
        + bytes([1, 1])
        + struct.pack("<iB", 6350, 3)
        + bytes([1])
    )
    prompt = (
        struct.pack("<HH", 9, 7)
        + struct.pack("<iB", 0, 5)
        + josl("Flip now")
        + josl("Flip the food and close the lid.")
    )
    return (
        header
        + josl("Weeknight Steak")
        + bytes([2])
        + struct.pack("<H", 1)
        + step
        + struct.pack("<H", 1)
        + prompt
    )


class FakeConnection:
    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.responses = list(responses or [])
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, *, timeout: float) -> bytes:
        if not self.responses:
            raise TimeoutError("no response")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def cloud_client() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(device_id=COMPANION_ID),
        config_host="api.walker-cloud.com",
        messaging_host="messaging.walker-cloud.com",
        user_agent="test",
        token=lambda: "token",
    )


class EnvelopeTests(unittest.TestCase):
    def test_targeted_envelope_round_trip(self) -> None:
        encoded = socket.encode_routed_message(COMPANION_ID, APPLIANCE_ID, 123, 0x0B, b"\x01\x03")
        decoded = socket.decode_routed_message(encoded)
        self.assertEqual(decoded.source_id, COMPANION_ID)
        self.assertEqual(decoded.target_id, APPLIANCE_ID)
        self.assertEqual(decoded.sequence, 123)
        self.assertEqual(decoded.message_version, 10)
        self.assertEqual(decoded.type_value, 0x0B)
        self.assertEqual(decoded.payload, b"\x01\x03")

    def test_invalid_ids_and_transport_length_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            socket.encode_routed_message("bad", APPLIANCE_ID, 1, 5)
        valid = bytearray(socket.encode_routed_message(COMPANION_ID, APPLIANCE_ID, 1, 5))
        valid[39:41] = struct.pack("<H", 99)
        with self.assertRaises(socket.WeberCloudSocketError):
            socket.decode_routed_message(bytes(valid))

    def test_invalid_routing_headers_are_rejected(self) -> None:
        with self.assertRaises(socket.WeberCloudSocketError):
            socket.decode_routed_message(b"short")
        valid = bytearray(socket.encode_routed_message(COMPANION_ID, APPLIANCE_ID, 1, 5))
        for index, value in ((0, 2), (1, 9), (18, 9)):
            changed = bytearray(valid)
            changed[index] = value
            with self.subTest(index=index), self.assertRaises(socket.WeberCloudSocketError):
                socket.decode_routed_message(bytes(changed))

    def test_wrong_length_ids_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            socket.encode_routed_message("00", APPLIANCE_ID, 1, 5)


class ProgramTests(unittest.TestCase):
    def test_v11_program_details_and_active_mapping(self) -> None:
        program = socket.decode_program_details(program_payload(), 11)
        self.assertEqual(program["title"], "Weeknight Steak")
        self.assertEqual(program["program_id"], str(PROGRAM_ID))
        self.assertEqual(program["plan_id"], 42)
        self.assertEqual(program["steps"][0]["target_temperature_c"], 63.5)
        self.assertEqual(program["steps"][0]["cook_mode"], "grill")
        self.assertEqual(program["prompts"][0]["instruction"], "Flip the food and close the lid.")
        self.assertEqual(program["unparsed_bytes"], 0)

        status = {
            "probes": [
                {
                    "probe_number": 1,
                    "program_id_hex": PROGRAM_ID.hex,
                    "plan_id": 42,
                    "step_id": 7,
                    "prompt_id": 9,
                    "time_remaining_s": 60,
                }
            ]
        }
        active = socket.active_cook_from_program(status, program)
        self.assertTrue(active["active"])
        self.assertEqual(active["current_instruction"], "Flip the food and close the lid.")

    def test_truncated_program_is_rejected(self) -> None:
        with self.assertRaises(socket.WeberCloudSocketError):
            socket.decode_program_details(program_payload()[:-1], 11)

    def test_v10_empty_program_and_inactive_mapping(self) -> None:
        payload = bytes([2, 3]) + PROGRAM_ID.bytes + b"\x07" + josl("Timer Cook") + b"\x00\x00\x00"
        program = socket.decode_program_details(payload, 10)
        self.assertEqual(program["plan_id"], 7)
        self.assertEqual(program["session_type"], "TIMED")
        self.assertEqual(program["steps"], [])
        self.assertFalse(socket.active_cook_from_program({"probes": []}, program)["active"])

    def test_invalid_text_and_excessive_counts_are_rejected(self) -> None:
        header = bytes([1, 0]) + PROGRAM_ID.bytes + struct.pack("<I", 1)
        with self.assertRaises(socket.WeberCloudSocketError):
            socket.decode_program_details(header + b"\x01\xff", 11)
        with self.assertRaises(socket.WeberCloudSocketError):
            socket.decode_program_details(
                header + josl("Too many") + b"\x00" + struct.pack("<H", 257),
                11,
            )
        with self.assertRaises(socket.WeberCloudSocketError):
            socket.decode_program_details(
                header + josl("Too many prompts") + b"\x00" + struct.pack("<HH", 0, 1025),
                11,
            )


class ClientTests(unittest.TestCase):
    def test_live_status_fetches_installed_program(self) -> None:
        probe = b"".join(
            [
                tlv(1, b"\x00"),
                tlv(3, PROGRAM_ID.bytes),
                tlv(5, struct.pack("<I", 60)),
                tlv(12, b"\x01"),
                tlv(16, struct.pack("<I", 42)),
                tlv(17, struct.pack("<H", 7)),
                tlv(18, struct.pack("<H", 9)),
            ]
        )
        status_payload = tlv(4, probe)
        responses = [
            socket.encode_routed_message(APPLIANCE_ID, COMPANION_ID, 1, 0x80, status_payload),
            socket.encode_routed_message(
                APPLIANCE_ID,
                COMPANION_ID,
                2,
                0x86,
                program_payload(),
                message_version=11,
            ),
        ]
        connection = FakeConnection(responses)
        client = socket.WeberCloudSocketClient(cloud_client(), subscribe_delay=0)
        client._connection = connection

        status = client.live_status(APPLIANCE_ID)

        self.assertEqual(status["active_cook"]["title"], "Weeknight Steak")
        self.assertEqual(client.received_types, [0x80, 0x86])
        sent = [socket.decode_routed_message(row) for row in connection.sent]
        self.assertEqual(
            [row.type_value for row in sent],
            [0x0E, 0x05, 0x09, 0x07, 0x0B, 0x0E, 0x09, 0x05, 0x07, 0x0B],
        )
        self.assertEqual(sent[2].payload[:2], b"\x15\x04")
        self.assertEqual(sent[-1].payload, b"\x01\x00")

    def test_connect_and_close_use_authenticated_runtime(self) -> None:
        connection = FakeConnection()
        connect_calls: list[tuple[str, dict]] = []
        client_module = types.ModuleType("websockets.sync.client")

        def connect(url: str, **kwargs):
            connect_calls.append((url, kwargs))
            return connection

        client_module.connect = connect
        sync_module = types.ModuleType("websockets.sync")
        sync_module.client = client_module
        websockets_module = types.ModuleType("websockets")
        websockets_module.sync = sync_module
        with mock.patch.dict(
            sys.modules,
            {
                "websockets": websockets_module,
                "websockets.sync": sync_module,
                "websockets.sync.client": client_module,
            },
        ):
            client = socket.WeberCloudSocketClient(cloud_client(), subscribe_delay=0)
            self.assertIs(client._connect(), connection)
            self.assertIs(client._connect(), connection)
            client.close()
        self.assertTrue(connection.closed)
        self.assertEqual(len(connect_calls), 1)
        self.assertEqual(
            connect_calls[0][0],
            "wss://messaging.walker-cloud.com/2/messaging/websocket/companion",
        )
        self.assertEqual(
            connect_calls[0][1]["additional_headers"]["Authorization"],
            "Bearer token",
        )

    def test_receive_skips_text_rejects_errors_and_closes_on_failure(self) -> None:
        accepted = socket.encode_routed_message(APPLIANCE_ID, COMPANION_ID, 1, 0x80, b"")
        connection = FakeConnection(["notice", accepted])  # type: ignore[list-item]
        client = socket.WeberCloudSocketClient(cloud_client())
        client._connection = connection
        self.assertEqual(client._receive_until({0x80}).type_value, 0x80)

        rejected = FakeConnection(
            [socket.encode_routed_message(APPLIANCE_ID, COMPANION_ID, 2, 0x87)]
        )
        client._connection = rejected
        with self.assertRaises(socket.WeberCloudSocketError):
            client._receive_until({0x80})

        failed = FakeConnection()
        client._connection = failed
        with self.assertRaises(TimeoutError):
            client._receive_until({0x80})
        self.assertTrue(failed.closed)

    def test_receive_deadline_and_close_errors_are_safe(self) -> None:
        client = socket.WeberCloudSocketClient(cloud_client(), timeout=1, subscribe_delay=0)
        client._connection = FakeConnection()
        with mock.patch.object(socket.time, "monotonic", side_effect=[0.0, 2.0]):
            with self.assertRaises(TimeoutError):
                client._receive_until({0x80})

        class BadClose:
            def close(self):
                raise RuntimeError("already gone")

        client._connection = BadClose()
        client.close()

    def test_live_status_ignores_unprogrammed_and_duplicate_slots(self) -> None:
        probe = tlv(1, b"\x00") + tlv(3, PROGRAM_ID.bytes)
        duplicate = tlv(1, b"\x00") + tlv(3, PROGRAM_ID.bytes)
        missing = tlv(1, b"\x01")
        status_payload = tlv(4, probe) + tlv(4, duplicate) + tlv(4, missing)
        responses = [
            socket.encode_routed_message(APPLIANCE_ID, COMPANION_ID, 1, 0x80, status_payload),
            socket.encode_routed_message(
                APPLIANCE_ID,
                COMPANION_ID,
                2,
                0x86,
                program_payload(),
                message_version=11,
            ),
        ]
        connection = FakeConnection(responses)
        client = socket.WeberCloudSocketClient(cloud_client(), subscribe_delay=0)
        client._connection = connection
        status = client.live_status(APPLIANCE_ID)
        self.assertEqual(len(status["programs"]), 1)
        self.assertIsNone(status["active_cook"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from custom_components.weber_connect import saber_frames as sf


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag & 0xFF, len(value) & 0xFF]) + value


class HexAndCrcTests(unittest.TestCase):
    def test_hex_to_bytes_strips_and_handles_prefix(self) -> None:
        self.assertEqual(sf.hex_to_bytes("0x00 11:22\n33\t"), b"\x00\x11\x22\x33")

    def test_hex_to_bytes_rejects_odd_length(self) -> None:
        with self.assertRaises(ValueError):
            sf.hex_to_bytes("abc")

    def test_bytes_to_hex_roundtrip(self) -> None:
        self.assertEqual(sf.hex_to_bytes(sf.bytes_to_hex(b"\x01\x02")), b"\x01\x02")

    def test_crc8_matches_reference(self) -> None:
        # CRC-8/MAXIM-DOW of "123456789" is 0xA1.
        self.assertEqual(sf.crc8(b"123456789"), 0xA1)
        self.assertEqual(sf.crc8(b""), 0)

    def test_type_name_covers_all_tables(self) -> None:
        self.assertEqual(sf.type_name(0x80), "INCOMING_STATUS")
        self.assertEqual(sf.type_name(0x0A), "OUTGOING_PAIRING_REQUEST")
        self.assertEqual(sf.type_name(0xEE), "UNKNOWN")


class BuilderValidationTests(unittest.TestCase):
    def test_build_handshake_body_validates_lengths(self) -> None:
        good = sf.build_handshake_body("aa" * 16, b"\x00" * 32)
        self.assertEqual(len(good), 48)
        with self.assertRaises(ValueError):
            sf.build_handshake_body("aa" * 15, b"\x00" * 32)
        with self.assertRaises(ValueError):
            sf.build_handshake_body("aa" * 16, b"\x00" * 31)

    def test_build_josl_string_truncates_from_front(self) -> None:
        result = sf.build_josl_string("x" * 40, max_byte_length=32)
        self.assertEqual(result[0], 32)
        self.assertEqual(len(result), 33)
        self.assertEqual(sf.build_josl_string(""), b"\x00")

    def test_build_pairing_body_validates(self) -> None:
        body = sf.build_pairing_body("aa" * 16, "bb" * 64, "Home")
        self.assertEqual(len(body), 16 + 64 + 5)
        with self.assertRaises(ValueError):
            sf.build_pairing_body("aa" * 15, "bb" * 64, "Home")
        with self.assertRaises(ValueError):
            sf.build_pairing_body("aa" * 16, "bb" * 63, "Home")


class EnvelopeTransportTests(unittest.TestCase):
    def test_command_frame_roundtrip_decodes_plaintext(self) -> None:
        frame = sf.build_command_frame(5, 11, 0x05, b"\x01\x02")
        decoded = sf.decode_hex_frame(sf.bytes_to_hex(frame))
        env = decoded["envelope"]
        self.assertTrue(decoded["length_ok"])
        self.assertTrue(env["crc_ok"])
        candidate = env["body_plain_candidate"]
        self.assertEqual(candidate["type_name"], "OUTGOING_FETCH_STATUS")
        self.assertEqual(candidate["message_version"], 11)

    def test_parse_appliance_payload_needs_two_bytes(self) -> None:
        self.assertIsNone(sf.parse_appliance_payload(b"\x0b"))

    def test_parse_envelope_rejects_bad_inputs(self) -> None:
        self.assertIsNone(sf.parse_envelope(b"\x00" * 4))
        self.assertIsNone(sf.parse_envelope(b"\x00" * 8))  # header != 0xAB
        # header ok but truncated body/footer
        self.assertIsNone(sf.parse_envelope(bytes([0xAB, 0, 0, 0, 0x10, 0x00]) + b"\x00" * 2))

    def test_parse_envelope_reports_crc_mismatch_and_skips_plain(self) -> None:
        payload = sf.build_appliance_payload(11, 0x05, b"")
        # message_count non-zero: plain candidate is skipped, crc will mismatch.
        header = bytes([0xAB, 0x01, 0x00, 0x00]) + len(payload).to_bytes(2, "little")
        raw = header + payload + bytes([0x00, 0x54])
        env = sf.parse_envelope(raw)
        self.assertIsNotNone(env)
        self.assertFalse(env.crc_ok)
        self.assertIsNone(env.body_plain_candidate)

    def test_parse_transport_frame_needs_six_bytes(self) -> None:
        self.assertIsNone(sf.parse_transport_frame(b"\x00" * 5))

    def test_decode_hex_frame_envelope_only(self) -> None:
        wrapped = sf.wrap_null_session(sf.build_appliance_payload(11, 0x05, b""))
        decoded = sf.decode_hex_frame(sf.bytes_to_hex(wrapped))
        self.assertIn("envelope", decoded)
        self.assertNotIn("sequence", decoded)

    def test_decode_hex_frame_raw_fallback(self) -> None:
        decoded = sf.decode_hex_frame("00:01")
        self.assertEqual(decoded["length"], 2)
        self.assertIn("raw_hex", decoded)


class KnownPayloadTests(unittest.TestCase):
    def test_pairing_response_payload(self) -> None:
        payload = bytes(range(16)) + bytes(range(64)) + bytes([0x01]) + b"\xff"
        parsed = sf.parse_known_payload(0x85, payload)
        self.assertEqual(parsed["kind"], "pairing_response")
        self.assertEqual(parsed["status"], "REJECTED")
        self.assertEqual(parsed["extra_hex"], "ff")

    def test_pairing_response_unknown_status(self) -> None:
        payload = bytes(80) + bytes([0x09])
        parsed = sf.parse_known_payload(0x85, payload)
        self.assertEqual(parsed["status"], "UNKNOWN")

    def test_unknown_type_returns_none(self) -> None:
        self.assertIsNone(sf.parse_known_payload(0x99, b"\x00"))

    def test_error_payload_full(self) -> None:
        payload = tlv(0, bytes([0x00])) + tlv(1, b"2.0.3") + b"\x05"
        parsed = sf.parse_error_payload(payload)
        self.assertEqual(parsed["error_type"], "UNSUPPORTED_MESSAGE_VERSION")
        self.assertEqual(parsed["appliance_software_version"], "2.0.3")
        self.assertEqual(parsed["unparsed_tail_hex"], "05")

    def test_error_payload_unknown_type_and_bad_version(self) -> None:
        payload = tlv(0, bytes([0x33])) + tlv(1, b"\xff\xfe")
        parsed = sf.parse_error_payload(payload)
        self.assertEqual(parsed["error_type"], "UNKNOWN")
        self.assertIsNone(parsed["appliance_software_version"])

    def test_error_payload_empty_type_and_overlong_record(self) -> None:
        # tag 0 present but zero-length value -> error_type_value stays None;
        # trailing record declares more bytes than remain -> parse loop breaks.
        payload = tlv(0, b"") + bytes([5, 3, 0x00])
        parsed = sf.parse_error_payload(payload)
        self.assertIsNone(parsed["error_type_value"])
        self.assertEqual(parsed["error_type"], "UNKNOWN")

    def test_error_payload_without_type_field(self) -> None:
        parsed = sf.parse_error_payload(tlv(1, b"9.9.9"))
        self.assertIsNone(parsed["error_type_value"])
        self.assertEqual(parsed["appliance_software_version"], "9.9.9")

    def test_error_payload_via_known_dispatch(self) -> None:
        parsed = sf.parse_known_payload(0x87, tlv(0, bytes([0xFF])))
        self.assertEqual(parsed["error_type"], "UNKNOWN")


class TlvTests(unittest.TestCase):
    def test_parse_tlv_captures_trailing_bytes(self) -> None:
        fields = sf.parse_tlv(tlv(1, b"\x01") + b"\x09")
        self.assertIn(-1, fields)
        self.assertEqual(fields[-1][-1], b"\x09")

    def test_parse_tlv_breaks_on_overlong_length(self) -> None:
        fields = sf.parse_tlv(bytes([1, 5, 0x00]))
        self.assertIn(-1, fields)


class CookSessionStatusTests(unittest.TestCase):
    def _full_probe(self) -> bytes:
        return b"".join(
            [
                tlv(1, bytes([0])),  # slot_index -> probe 1
                tlv(2, bytes([7])),  # session id
                tlv(3, b"\xaa\xbb"),  # program id
                tlv(16, (100).to_bytes(4, "little")),  # plan id u32
                tlv(5, (600).to_bytes(4, "little")),  # time remaining
                tlv(6, (30).to_bytes(4, "little")),  # time elapsed
                tlv(17, (2).to_bytes(2, "little")),  # step id u16
                tlv(8, (5).to_bytes(4, "little")),
                tlv(9, (1).to_bytes(4, "little")),
                tlv(18, (3).to_bytes(2, "little")),  # prompt id u16
                tlv(12, bytes([5])),  # state ACTIVE
                tlv(19, bytes([1])),  # probe type WIRED
                tlv(10, (655).to_bytes(2, "little", signed=True)),  # 65.5C probe temp
                tlv(20, b"TESTSERIAL"),
                tlv(21, b"SKU1"),
                tlv(22, bytes([88])),  # battery
                tlv(13, (4).to_bytes(2, "little")),  # active event
                tlv(23, (200).to_bytes(2, "little", signed=True)),  # segment temp
                tlv(23, b"\x01"),  # too short -> None, filtered
                tlv(24, (300).to_bytes(2, "little", signed=True)),  # case temp
                tlv(25, (250).to_bytes(2, "little", signed=True)),  # ambient temp
            ]
        )

    def _minimal_probe(self) -> bytes:
        # No tag19/tag16/tag17/tag18: exercise the `or` fallbacks and Nones.
        return b"".join(
            [
                tlv(4, bytes([2])),  # probe_type fallback + plan_id fallback
                tlv(7, bytes([9])),  # step_id fallback
                tlv(11, bytes([4])),  # prompt_id fallback
            ]
        )

    def _timed_session(self) -> bytes:
        return b"".join(
            [
                tlv(1, bytes([1])),
                tlv(2, bytes([8])),
                tlv(5, (900).to_bytes(4, "little")),
                tlv(6, (60).to_bytes(4, "little")),
                tlv(12, bytes([5])),
            ]
        )

    def _timer(self) -> bytes:
        return b"".join(
            [
                tlv(1, bytes([2])),
                tlv(2, bytes([9])),
                tlv(5, (300).to_bytes(4, "little")),
                tlv(6, (30).to_bytes(4, "little")),
                tlv(12, bytes([6])),
            ]
        )

    def _burner(self) -> bytes:
        return b"".join(
            [
                tlv(1, bytes([0])),
                tlv(2, bytes([3])),
                tlv(3, bytes([2])),
                tlv(4, bytes([75])),
                tlv(5, bytes([60])),
                tlv(6, bytes([1])),
                tlv(7, bytes([2])),
            ]
        )

    def test_full_status_roundtrip(self) -> None:
        payload = b"".join(
            [
                tlv(4, self._full_probe()),
                tlv(4, self._minimal_probe()),
                tlv(5, self._timed_session()),
                tlv(6, self._timer()),
                tlv(7, self._burner()),
                tlv(1, (-32768).to_bytes(2, "little", signed=True)),  # sentinel -> None
                tlv(2, (400).to_bytes(2, "little", signed=True)),
                tlv(3, bytes([1])),  # cook mode
                tlv(8, b"\xde\xad"),
                tlv(9, (12).to_bytes(4, "little")),
                tlv(10, (99).to_bytes(8, "little")),
                tlv(11, bytes([170])),
                tlv(12, bytes([1])),
                tlv(13, (410).to_bytes(2, "little", signed=True)),
                tlv(14, (205).to_bytes(2, "little", signed=True)),
                tlv(15, (96).to_bytes(2, "little", signed=True)),
            ]
        )
        parsed = sf.parse_cook_session_status_payload(payload)
        self.assertEqual(parsed["kind"], "cook_session_status")
        self.assertEqual(parsed["probe_count"], 2)

        full = parsed["probes"][0]
        self.assertEqual(full["probe_number"], 1)
        self.assertEqual(full["state"], "ACTIVE")
        self.assertEqual(full["probe_type"], "WIRED")
        self.assertEqual(full["serial_number"], "TESTSERIAL")
        self.assertEqual(full["battery_level"], 88)
        self.assertEqual(full["probe_temp_c"], 65.5)
        self.assertEqual(full["plan_id"], 100)
        self.assertEqual(full["step_id"], 2)
        self.assertEqual(full["prompt_id"], 3)
        self.assertEqual(len(full["segment_temps"]), 1)

        minimal = parsed["probes"][1]
        self.assertIsNone(minimal["slot_index"])
        self.assertEqual(minimal["label"], "Probe")
        self.assertEqual(minimal["plan_id"], 2)  # fallback tag4
        self.assertEqual(minimal["step_id"], 9)
        self.assertEqual(minimal["prompt_id"], 4)
        self.assertEqual(minimal["probe_type"], "WIRELESS")

        # sentinel deci-celsius sets the *_c/*_f pair to None.
        self.assertIsNone(parsed["target_cavity_temp_c"])
        self.assertEqual(parsed["actual_cavity_temp_c"], 41.0)
        self.assertEqual(parsed["display_cavity_temp_f"], 205)
        self.assertEqual(parsed["cook_mode_value"], 1)
        self.assertEqual(parsed["cook_mode"], "grill")
        self.assertEqual(parsed["simple_intensity_raw"], 170)
        self.assertEqual(parsed["simple_intensity"], 7)
        self.assertEqual(parsed["timed_sessions"][0]["slot_number"], 2)
        self.assertEqual(parsed["timed_sessions"][0]["time_remaining_s"], 900)
        self.assertEqual(parsed["timed_sessions"][0]["state"], "ACTIVE")
        self.assertEqual(parsed["timers"][0]["slot_number"], 3)
        self.assertEqual(parsed["timers"][0]["time_remaining_s"], 300)
        self.assertEqual(parsed["timers"][0]["state"], "PAUSED")
        self.assertEqual(parsed["burners"][0]["number"], 1)
        self.assertEqual(parsed["burners"][0]["type"], "gas_burner")
        self.assertEqual(parsed["burners"][0]["state"], "on")
        self.assertEqual(parsed["burners"][0]["target_intensity_raw"], 75)
        self.assertEqual(parsed["burners"][0]["target_intensity"], 3)
        self.assertEqual(parsed["burners"][0]["actual_intensity"], 3)
        self.assertTrue(parsed["burners"][0]["flame_sensed"])
        self.assertTrue(parsed["burners"][0]["locked"])

    def test_ir_burner_uses_two_intensity_levels_and_missing_state_is_none(self) -> None:
        parsed = sf.parse_burner_status_tlv(
            tlv(1, bytes([0])) + tlv(2, bytes([7])) + tlv(4, bytes([255])) + tlv(5, bytes([0]))
        )

        self.assertIsNone(parsed["state"])
        self.assertEqual(parsed["target_intensity"], 2)
        self.assertEqual(parsed["actual_intensity"], 1)

    def test_display_cavity_temp_uses_deci_celsius_when_unit_tags_are_absent(
        self,
    ) -> None:
        payload = tlv(2, (400).to_bytes(2, "little", signed=True))

        parsed = sf.parse_cook_session_status_payload(payload)

        self.assertEqual(parsed["display_cavity_temp_c"], 40.0)
        self.assertEqual(parsed["display_cavity_temp_f"], 104.0)

    def test_status_with_unparsed_tail(self) -> None:
        payload = tlv(3, bytes([1])) + b"\x07"
        parsed = sf.parse_cook_session_status_payload(payload)
        self.assertEqual(parsed["unparsed_tail_hex"], "07")

    def test_probe_tlv_unparsed_tail(self) -> None:
        row = sf.parse_probe_session_status_tlv(tlv(1, bytes([0])) + b"\x07")
        self.assertEqual(row["unparsed_tail_hex"], "07")

    def test_text_decode_failure_returns_none(self) -> None:
        row = sf.parse_probe_session_status_tlv(tlv(1, bytes([0])) + tlv(20, b"\xff\xfe"))
        self.assertIsNone(row["serial_number"])

    def test_status_dispatch_through_known_payload(self) -> None:
        parsed = sf.parse_known_payload(0x80, tlv(3, bytes([2])))
        self.assertEqual(parsed["kind"], "cook_session_status")

    def test_appliance_status_reports_hub_battery_and_charging_state(self) -> None:
        parsed = sf.parse_appliance_status_payload(tlv(1, bytes([87])) + tlv(2, bytes([1])))

        self.assertEqual(parsed["kind"], "appliance_status")
        self.assertEqual(parsed["battery_level"], 87)
        self.assertTrue(parsed["is_charging"])
        minimal = sf.parse_known_payload(0x83, tlv(2, bytes([0])))
        self.assertEqual(minimal["kind"], "appliance_status")
        self.assertIsNone(minimal["battery_level"])
        self.assertFalse(minimal["is_charging"])

    def test_appliance_status_reports_connectivity_fuel_and_versions(self) -> None:
        parsed = sf.parse_appliance_status_payload(
            b"".join(
                [
                    tlv(4, bytes([3])),
                    tlv(10, (-61).to_bytes(4, "little", signed=True)),
                    tlv(14, b"rev-b"),
                    tlv(15, bytes([2])),
                    tlv(17, bytes([1])),
                    tlv(19, b"2.0.3_7398"),
                    tlv(20, bytes([5])),
                    tlv(27, bytes([18])),
                ]
            )
        )

        self.assertEqual(parsed["cloud_connection_status"], "connected")
        self.assertEqual(parsed["wifi_signal_strength"], -61)
        self.assertEqual(parsed["wifi_connection_status"], "connected")
        self.assertEqual(parsed["device_state"], "active")
        self.assertEqual(parsed["software_version"], "2.0.3_7398")
        self.assertEqual(parsed["hardware_version"], "rev-b")
        self.assertEqual(parsed["fuel_level"], "low")
        self.assertEqual(parsed["fuel_percent"], 18)

    def test_appliance_status_preserves_unknown_and_unparsed_tail(self) -> None:
        parsed = sf.parse_appliance_status_payload(b"\x07")

        self.assertIsNone(parsed["battery_level"])
        self.assertIsNone(parsed["is_charging"])
        self.assertEqual(parsed["unparsed_tail_hex"], "07")


if __name__ == "__main__":
    unittest.main()

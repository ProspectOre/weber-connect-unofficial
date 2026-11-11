"""OEM-derived program fixtures and stale-target regression tests."""

import struct

import pytest

from custom_components.weber_connect.probe_targets import ProbeTargets
from custom_components.weber_connect.programs import (
    ProbeTarget,
    ProgramDecodeError,
    parse_probe_program,
)
from custom_components.weber_connect.saber_frames import parse_probe_session_status_tlv

PROGRAM_ID = bytes.fromhex("00112233445566778899aabbccddeeff")


def program_frame(version=10, *, slot=2, plan=0, steps=None, prompts=False):
    """Synthetic OEM wire layout, independent of the production encoder."""
    count = "H" if version >= 11 else "B"
    if steps is None:
        steps = [(0, [(3, 500)], 2), (1, [(9, 400), (3, 600)], 1)]
    data = bytes([1, slot]) + PROGRAM_ID + struct.pack("<I" if version >= 11 else "<B", plan)
    data += b"\x04Test\x00" + struct.pack("<" + count, len(steps))
    for step, triggers, requirement in steps:
        data += struct.pack("<" + count + "IhB", step, 120000, -32768, len(triggers))
        data += b"".join(struct.pack("<iB", value, kind) for kind, value in triggers)
        data += bytes([requirement]) + (b"\x01" if version >= 10 else b"")
    data += struct.pack("<" + count, int(prompts))
    if prompts:
        data += struct.pack("<" + count * 2 + "iB", 1, 0, 1000, 1)
        data += b"\x02Hi\x04Done"
    return data


def probe(**changes):
    return {
        "slot_index": 2,
        "probe_number": 3,
        "session_id": 7,
        "program_id_hex": PROGRAM_ID.hex(),
        "plan_id": 0,
        "step_id": 0,
        "state": "ACTIVE",
        **changes,
    }


@pytest.mark.parametrize("version", [5, 9, 10, 11])
def test_oem_versioned_program_thresholds_and_ranges(version):
    result = parse_probe_program(program_frame(version, prompts=True), version)
    assert (result.slot, result.program_id, result.plan_id) == (2, PROGRAM_ID.hex(), 0)
    assert result.targets == {0: ProbeTarget(50), 1: ProbeTarget(60, 40)}
    if version == 11:
        wide = parse_probe_program(
            program_frame(version, plan=0x12345678, steps=[(1000, [(3, 0)], 2)]), version
        )
        assert wide.plan_id == 0x12345678
        assert wide.targets == {1000: ProbeTarget(0)}


@pytest.mark.parametrize(
    ("triggers", "target"),
    [
        ([], None),
        ([(9, 300)], None),
        ([(2, 500), (225, 500), (0, 0)], None),
        ([(3, -32768)], ProbeTarget(-3276.8)),
        ([(3, 500), (3, 600)], None),
        ([(3, 500), (9, 300), (9, 400)], None),
        ([(3, 500), (9, 600)], None),
        ([(3, 500), (3, 500)], ProbeTarget(50)),
        ([(3, 500), (9, 500)], ProbeTarget(50, 50)),
    ],
)
def test_target_semantics_do_not_invent_a_threshold(triggers, target):
    result = parse_probe_program(program_frame(steps=[(0, triggers, 2)]), 10)
    assert result.targets[0] == target


def test_live_hub_range_alert_uses_prompt_thresholds():
    # Sanitized Connect Hub 2.0.3_7398 response for an OEM 70-100°F alert.
    frame = bytes.fromhex(
        "01020000000000000000000000000000000002095072696d6974697665"
        "0001020000000000800100000000040200020302d3000000090000"
        "040279010000030000"
    )
    result = parse_probe_program(frame, 10)
    assert result.targets == {2: ProbeTarget(37.7, 21.1)}
    assert result.plan_id == 2


@pytest.mark.parametrize("version", [5, 10, 11])
def test_prompt_triggers_belong_only_to_the_referenced_step(version):
    frame = program_frame(version, steps=[(0, [], 2), (1, [(3, 600)], 2)])
    width = 2 if version >= 11 else 1
    count = "H" if version >= 11 else "B"
    frame = frame[:-width] + struct.pack("<" + count, 1)
    frame += struct.pack("<" + count * 2 + "iB", 1, 0, 500, 3) + b"\x00\x00"
    assert parse_probe_program(frame, version).targets == {
        0: ProbeTarget(50),
        1: ProbeTarget(60),
    }
    unknown_step = frame[: -(width + 7)] + struct.pack("<" + count, 3) + frame[-7:]
    with pytest.raises(ProgramDecodeError, match="unknown program step"):
        parse_probe_program(unknown_step, version)


def test_program_decoder_rejects_every_truncated_prefix_and_extra_data():
    frame = program_frame(11, prompts=True)
    for length in range(len(frame)):
        with pytest.raises(ProgramDecodeError):
            parse_probe_program(frame[:length], 11)
    with pytest.raises(ProgramDecodeError, match="trailing"):
        parse_probe_program(frame + b"\x00", 11)


@pytest.mark.parametrize("version", [0, 4, 12, 255])
def test_program_decoder_rejects_unknown_version(version):
    with pytest.raises(ProgramDecodeError, match="format"):
        parse_probe_program(program_frame(), version)


def test_program_decoder_rejects_wrong_sessions_bad_text_counts_and_duplicate_steps():
    frame = program_frame()
    for bad in [b"\x02" + frame[1:], frame[:1] + b"\x10" + frame[2:]]:
        with pytest.raises(ProgramDecodeError, match="supported probe"):
            parse_probe_program(bad, 10)
    with pytest.raises(ProgramDecodeError, match="text"):
        parse_probe_program(frame[:20] + b"\xff" + frame[21:], 10)
    with pytest.raises(ProgramDecodeError, match="Duplicate"):
        parse_probe_program(program_frame(steps=[(0, [], 1), (0, [], 2)]), 10)
    with pytest.raises(ProgramDecodeError, match="requirement"):
        parse_probe_program(program_frame(steps=[(0, [], 0)]), 10)
    wide = program_frame(11, steps=[])
    with pytest.raises(ProgramDecodeError, match="steps"):
        parse_probe_program(wide[:28] + b"\xff\xff" + wide[30:], 11)
    with pytest.raises(ProgramDecodeError, match="prompts"):
        parse_probe_program(wide[:-2] + b"\xff\xff", 11)
    with pytest.raises(ProgramDecodeError, match="format"):
        parse_probe_program(b"\x00" * 65534, 10)


def test_current_step_changes_between_single_range_and_absent_target():
    cache = ProbeTargets()
    row = probe(program_id_hex=PROGRAM_ID.hex(":"))
    assert cache.sync([row], 10, 0) == [2]
    assert row["target_temperature_c"] is None
    cache.requested(2, 0)
    assert cache.sync([row], 10, 1) == []
    cache.receive(program_frame(), 10, 1)
    assert cache.sync([row], 10, 2) == []
    assert row["target_temperature_c"] == 50
    row["step_id"] = 1
    cache.sync([row], 10, 3)
    assert row["target_temperature_c"] is None
    assert (row["target_lower_temperature_c"], row["target_upper_temperature_c"]) == (40, 60)
    row["step_id"] = 2
    cache.sync([row], 10, 4)
    assert row["target_lower_temperature_c"] is None
    assert row["target_upper_temperature_c"] is None
    assert cache.sync([row], 10, 30) == [2]
    cache.requested(2, 30)
    cache.receive(program_frame(steps=[(2, [(9, 500), (3, 500)], 2)]), 10, 31)
    cache.sync([row], 10, 32)
    assert row["target_temperature_c"] == 50
    cache.sync([row], 10, 91)
    assert row["target_temperature_c"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"slot_index": True},
        {"slot_index": 16},
        {"slot_index": -1},
        {"session_id": None},
        {"plan_id": None},
        {"step_id": None},
        {"state": "COMPLETE"},
        {"state": "PROBED"},
        {"program_id_hex": None},
        {"program_id_hex": "1"},
        {"program_id_hex": "g" * 32},
    ],
)
def test_invalid_or_finished_session_does_not_request_or_retain_targets(change):
    cache = ProbeTargets()
    row = probe(**change, target_temperature_c=50)
    assert cache.sync([row], 10, 0) == []
    assert row["target_temperature_c"] is None


def test_oem_manual_alert_uses_zero_uuid_with_active_session_identity():
    # Connect Hub firmware 2.0.3_7398 reports an all-zero UUID for manual alerts.
    cache = ProbeTargets()
    row = probe(program_id_hex=bytes(16).hex(":"), step_id=2, state="ACTIVE_FIXED")
    assert cache.sync([row], 10, 0) == [2]
    cache.requested(2, 0)
    frame = program_frame(steps=[(2, [(3, 489)], 2)])
    frame = frame[:2] + bytes(16) + frame[18:]
    cache.receive(frame, 10, 1)
    cache.sync([row], 10, 2)
    assert row["target_temperature_c"] == 48.9
    row["state"] = "PROBED"
    assert cache.sync([row], 10, 3) == []
    assert row["target_temperature_c"] is None


def test_unsupported_version_duplicate_slots_reconnect_and_retired_replies():
    cache = ProbeTargets()
    row = probe()
    assert cache.sync([row], 12, 0) == []
    assert cache.sync([row, probe()], 10, 0) == [2]
    cache.requested(2, 0)
    row["session_id"] = 8
    assert cache.sync([row], 10, 1) == []
    cache.receive(program_frame(), 10, 2)  # Retired owner; no new read overlaps it.
    assert cache.sync([row], 10, 2) == [2]
    assert row["target_temperature_c"] is None
    cache.requested(2, 2)
    cache.receive(program_frame(11), 11, 3)
    cache.sync([row], 10, 4)
    assert row["target_temperature_c"] is None
    cache.receive(program_frame(), 10, 4)
    cache.sync([row], 10, 5)
    assert row["target_temperature_c"] == 50
    cache.clear()
    cache.receive(program_frame(), 10, 6)
    assert cache.sync([row], 10, 6) == [2]
    assert row["target_temperature_c"] is None
    cache.requested(2, 6)
    cache.sync([], 10, 7)
    cache.receive(program_frame(), 10, 8)


def test_ambiguous_error_suspends_optional_reads_across_reconnect():
    cache = ProbeTargets()
    row = probe()
    cache.controller_error(0)
    assert not cache.requests_suspended
    cache.sync([row], 10, 0)
    cache.controller_error(1)
    assert not cache.requests_suspended
    cache.requested(2, 1)
    cache.controller_error(31)
    assert not cache.requests_suspended
    cache.receive(program_frame(), 10, 31)  # Drain the expired read before retrying.
    cache.requested(2, 31)
    cache.controller_error(32)
    assert cache.requests_suspended
    cache.clear()
    assert cache.sync([row], 10, 33) == []
    assert row["target_temperature_c"] is None


@pytest.mark.parametrize(
    "bad",
    [
        b"short",
        program_frame(plan=1),
        program_frame()[:2] + b"\xff" * 16 + program_frame()[18:],
    ],
)
def test_malformed_or_wrong_program_cannot_supply_a_target(bad):
    cache = ProbeTargets()
    row = probe()
    cache.sync([row], 10, 0)
    cache.requested(2, 0)
    cache.receive(program_frame(), 10, 1)
    cache.sync([row], 10, 2)
    assert row["target_temperature_c"] == 50
    cache.sync([row], 10, 30)
    cache.requested(2, 30)
    cache.receive(bad, 10, 31)
    cache.receive(program_frame(), 10, 31)
    assert cache.sync([row], 10, 32) == []
    assert row["target_temperature_c"] is None


def test_unrequested_slots_expired_windows_and_duplicates_do_not_replace_targets():
    cache = ProbeTargets()
    row = probe()
    cache.sync([row], 10, 0)
    cache.receive(b"short", 10, 0)
    cache.requested(2, 0)
    cache.receive(program_frame(slot=1), 10, 1)
    cache.receive(program_frame(), 10, 30)
    cache.receive(b"short", 10, 30)
    cache.sync([row], 10, 30)
    assert row["target_temperature_c"] is None
    cache.requested(2, 30)
    cache.receive(program_frame(), 10, 31)
    cache.receive(program_frame(steps=[(0, [(3, 900)], 2)]), 10, 32)
    cache.sync([row], 10, 33)
    assert row["target_temperature_c"] == 50


def test_modern_zero_ids_override_legacy_fields_and_invalid_widths_do_not_fallback():
    def tlv(tag, data):
        return bytes([tag, len(data)]) + data

    legacy = tlv(4, b"\x03") + tlv(7, b"\x04") + tlv(11, b"\x05")
    modern = tlv(16, bytes(4)) + tlv(17, bytes(2)) + tlv(18, bytes(2)) + tlv(19, b"\x00")
    parsed = parse_probe_session_status_tlv(legacy + modern)
    assert [parsed[key] for key in ["plan_id", "step_id", "prompt_id", "probe_type_value"]] == [
        0
    ] * 4
    invalid = parse_probe_session_status_tlv(legacy + tlv(16, b"\x01"))
    assert invalid["plan_id"] is None
    assert invalid["probe_type_value"] == 1
    malformed_type = parse_probe_session_status_tlv(legacy + tlv(19, b"\x01\x00"))
    assert malformed_type["probe_type_value"] is None
    malformed_event = parse_probe_session_status_tlv(tlv(13, b"\x01"))
    assert malformed_event["active_events"] == [None]


@pytest.mark.parametrize("transition", ["replace", "disappear", "reuse_all_ids"])
def test_retired_session_reply_is_drained_before_requesting_replacement(transition):
    cache = ProbeTargets()
    row = probe()
    cache.sync([row], 10, 0)
    cache.requested(2, 0)
    if transition != "replace":
        cache.sync([], 10, 1)
    replacement = probe(session_id=7 if transition == "reuse_all_ids" else 8)
    assert cache.sync([replacement], 10, 2) == []
    cache.receive(program_frame(), 10, 3)
    assert cache.sync([replacement], 10, 4) == [2]
    assert replacement["target_temperature_c"] is None
    cache.requested(2, 4)
    cache.receive(program_frame(steps=[(0, [(3, 600)], 2)]), 10, 5)
    cache.sync([replacement], 10, 6)
    assert replacement["target_temperature_c"] == 60


def test_expired_pending_read_never_overlaps_a_retry():
    cache = ProbeTargets()
    row = probe()
    cache.sync([row], 10, 0)
    cache.requested(2, 0)
    cache.receive(b"malformed unidentifiable reply", 10, 31)
    assert cache.sync([row], 10, 31) == []
    assert row["target_temperature_c"] is None
    cache.receive(program_frame(), 10, 32)
    assert cache.sync([row], 10, 33) == [2]
    assert row["target_temperature_c"] is None


def test_controller_error_recognizes_pending_read_for_disappeared_session():
    cache = ProbeTargets()
    cache.sync([probe()], 10, 0)
    cache.requested(2, 0)
    cache.sync([], 10, 1)
    cache.controller_error(2)
    assert cache.requests_suspended

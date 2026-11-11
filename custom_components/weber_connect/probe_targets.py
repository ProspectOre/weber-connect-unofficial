"""Match optional program replies to the current physical probe sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import MAX_PROBE_SLOTS
from .programs import (
    SUPPORTED_PROGRAM_VERSIONS,
    ProbeProgram,
    ProbeTarget,
    ProgramDecodeError,
    parse_probe_program,
)
from .state import ACTIVE_SESSION_STATES

PROGRAM_REFRESH_SECONDS = 30.0
PROGRAM_MAX_AGE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class _SessionKey:
    session_id: int
    program_id: str
    plan_id: int
    message_version: int


@dataclass(slots=True)
class _Record:
    key: _SessionKey
    program: ProbeProgram | None = None
    expires_at: float = 0.0
    request_after: float = 0.0
    pending_until: float | None = None


class ProbeTargets:
    """Keep bounded program reads separate from the required temperature stream."""

    def __init__(self) -> None:
        self._records: dict[int, _Record] = {}
        self.requests_suspended = False

    def clear(self) -> None:
        self._records.clear()

    def sync(self, probes: list[dict[str, Any]], version: int, now: float) -> list[int]:
        """Apply only current step targets and return bounded probe slots to read."""

        active: dict[int, _Record] = {}
        due: list[int] = []
        for probe in probes:
            probe["target_temperature_c"] = None
            probe["target_lower_temperature_c"] = None
            probe["target_upper_temperature_c"] = None
            slot = probe.get("slot_index")
            session_id = probe.get("session_id")
            program_id = probe.get("program_id_hex")
            if isinstance(program_id, str):
                program_id = program_id.replace(":", "")
            plan_id = probe.get("plan_id")
            step_id = probe.get("step_id")
            if (
                self.requests_suspended
                or version not in SUPPORTED_PROGRAM_VERSIONS
                or type(slot) is not int
                or not 0 <= slot < MAX_PROBE_SLOTS
                or slot in active
                or type(session_id) is not int
                or type(plan_id) is not int
                or type(step_id) is not int
                or probe.get("state") not in ACTIVE_SESSION_STATES
                or not isinstance(program_id, str)
                or len(program_id) != 32
                or not all(char in "0123456789abcdef" for char in program_id)
            ):
                continue
            key = _SessionKey(session_id, program_id, plan_id, version)
            record = self._records.get(slot)
            if record is None or record.key != key:
                record = _Record(key)
            active[slot] = record
            if record.program is not None and now < record.expires_at:
                target = record.program.targets.get(step_id)
                if target is not None:
                    self._apply(probe, target)
            if now >= record.request_after:
                due.append(slot)
        self._records = active
        return due

    @staticmethod
    def _apply(probe: dict[str, Any], target: ProbeTarget) -> None:
        if target.lower_c is None or target.lower_c == target.upper_c:
            probe["target_temperature_c"] = target.upper_c
        else:
            probe["target_lower_temperature_c"] = target.lower_c
            probe["target_upper_temperature_c"] = target.upper_c

    def requested(self, slot: int, now: float) -> None:
        record = self._records[slot]
        record.request_after = now + PROGRAM_REFRESH_SECONDS
        record.pending_until = record.request_after

    def controller_error(self, now: float) -> None:
        """Suspend optional reads after an ambiguous error, without swallowing it.

        Controller sequences do not echo requests and errors carry no request
        identity. Preserve normal error/recovery handling, but do not repeatedly
        disrupt required telemetry by retrying a potentially unsupported read.
        Reloading the integration permits a new attempt.
        """
        if any(
            record.pending_until is not None and now < record.pending_until
            for record in self._records.values()
        ):
            self.requests_suspended = True
            self.clear()

    def receive(self, payload: bytes, version: int, now: float) -> None:
        """Match embedded identity during the current request's bounded window.

        The controller uses an independent inbound sequence. Neither that
        sequence nor the response payload echoes our request/session ID.
        """
        try:
            program = parse_probe_program(payload, version)
        except ProgramDecodeError:
            # A malformed response cannot identify which pending read failed.
            for pending_record in self._records.values():
                if pending_record.pending_until is not None and now < pending_record.pending_until:
                    pending_record.program = None
                    pending_record.pending_until = None
            return
        record = self._records.get(program.slot)
        if (
            record is None
            or record.pending_until is None
            or now >= record.pending_until
            or record.key.message_version != version
        ):
            return
        record.pending_until = None
        if program.program_id != record.key.program_id or program.plan_id != record.key.plan_id:
            record.program = None
            return
        record.program = program
        record.expires_at = now + PROGRAM_MAX_AGE_SECONDS

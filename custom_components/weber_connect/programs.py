"""Bounded decoding of read-only Weber cooking-program details."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .const import MAX_PROBE_SLOTS

PROGRAM_REQUEST = 0x0B
PROGRAM_RESPONSE = 0x86
PROBE_SESSION = 1
SUPPORTED_PROGRAM_VERSIONS = range(5, 12)
MAX_PROGRAM_STEPS = 256
MAX_PROGRAM_PROMPTS = 512


class ProgramDecodeError(ValueError):
    """A program response cannot safely describe probe targets."""


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    """An upper threshold, or the two boundaries of an OEM target range."""

    upper_c: float
    lower_c: float | None = None


@dataclass(frozen=True, slots=True)
class ProbeProgram:
    """Only the identity and target information needed for telemetry."""

    slot: int
    program_id: str
    plan_id: int
    targets: dict[int, ProbeTarget | None]


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.payload):
            raise ProgramDecodeError("Truncated program details.")
        result = self.payload[self.offset : end]
        self.offset = end
        return result

    def number(self, fmt: str) -> int:
        return int(struct.unpack("<" + fmt, self.take(struct.calcsize(fmt)))[0])

    def text(self) -> None:
        # Validate but do not retain user-supplied program titles or prompt text.
        try:
            self.take(self.number("B")).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProgramDecodeError("Invalid program text.") from exc

    def trigger(self) -> tuple[int, int]:
        value = self.number("i")
        return self.number("B"), value


def _target(triggers: list[tuple[int, int]]) -> ProbeTarget | None:
    # OEM GetCookTargetsInfoUseCaseKt uses ProbeTempAbove (3) and
    # ProbeTempBelow (9). A floor alone is not displayed as a target.
    upper = {value for kind, value in triggers if kind == 3}
    lower = {value for kind, value in triggers if kind == 9}
    if len(upper) != 1 or len(lower) > 1:
        return None
    high = next(iter(upper))
    low = next(iter(lower)) if lower else None
    # Temperature triggers are signed deci-C, distinct from duration triggers.
    if low is not None and low > high:
        return None
    return ProbeTarget(high / 10.0, low / 10.0 if low is not None else None)


def parse_probe_program(payload: bytes, message_version: int) -> ProbeProgram:
    """Parse OEM IncomingProgramDetails without retaining unrelated program data."""

    if message_version not in SUPPORTED_PROGRAM_VERSIONS or len(payload) > 65533:
        raise ProgramDecodeError("Unsupported program format.")
    reader = _Reader(payload)
    session_type, slot = reader.number("B"), reader.number("B")
    if session_type != PROBE_SESSION or slot >= MAX_PROBE_SLOTS:
        raise ProgramDecodeError("Program details do not identify a supported probe.")
    program_id = reader.take(16).hex()
    wide = message_version >= 11
    plan_id = reader.number("I" if wide else "B")
    reader.text()
    reader.number("B")  # ETA curve, not a target.
    count_format = "H" if wide else "B"
    step_count = reader.number(count_format)
    if step_count > MAX_PROGRAM_STEPS:
        raise ProgramDecodeError("Too many program steps.")
    step_triggers: dict[int, list[tuple[int, int]]] = {}
    for _ in range(step_count):
        step_id = reader.number(count_format)
        if step_id in step_triggers:
            raise ProgramDecodeError("Duplicate program step.")
        reader.number("I")  # Base duration.
        reader.number("h")  # Cavity target, not the food probe target.
        triggers = [reader.trigger() for _ in range(reader.number("B"))]
        requirement = reader.number("B")
        if requirement not in (1, 2):
            raise ProgramDecodeError("Unsupported program trigger requirement.")
        if message_version >= 10:
            reader.number("B")  # Cook mode, added in ROCKET (version 10).
        step_triggers[step_id] = triggers
    prompt_count = reader.number(count_format)
    if prompt_count > MAX_PROGRAM_PROMPTS:
        raise ProgramDecodeError("Too many program prompts.")
    for _ in range(prompt_count):
        reader.number(count_format)  # Prompt ID.
        step_id = reader.number(count_format)
        trigger = reader.trigger()
        if step_id not in step_triggers:
            raise ProgramDecodeError("Prompt references an unknown program step.")
        # OEM SdkModelExtensionsKt.allTriggers combines exit criteria and
        # prompt triggers. Manual range alerts store both limits in prompts.
        step_triggers[step_id].append(trigger)
        reader.text()
        reader.text()
    if reader.offset != len(payload):
        raise ProgramDecodeError("Unexpected trailing program data.")
    return ProbeProgram(
        slot,
        program_id,
        plan_id,
        {step_id: _target(triggers) for step_id, triggers in step_triggers.items()},
    )

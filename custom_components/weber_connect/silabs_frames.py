"""Structural SiLabs appliance transport frames.

The header length counts the wire body, including nonce and tag when encrypted.
Authentication and message interpretation are handled in ``silabs_secure``.
"""

from __future__ import annotations

from dataclasses import dataclass


class SiLabsFrameError(ValueError):
    """The transport header is malformed or incomplete."""


class SiLabsEncryptedFrame(SiLabsFrameError):
    """The body requires an authenticated SiLabs session."""


@dataclass(frozen=True, slots=True)
class SiLabsFrame:
    """A validated transport envelope, without inferred message semantics."""

    sequence: int
    length: int
    encrypted: bool
    body: bytes
    boot_count: int | None = None

    def plaintext_body(self) -> bytes:
        """Return only a body explicitly marked as plaintext."""

        if self.encrypted:
            raise SiLabsEncryptedFrame("An authenticated SiLabs session is required.")
        return self.body


def parse_incoming_frame(data: bytes) -> SiLabsFrame:
    """Validate length and the incoming 28-bit sequence / 4-bit boot count."""

    if len(data) < 7:
        raise SiLabsFrameError("SiLabs frame is shorter than its transport header.")
    packed = int.from_bytes(data[:4], "little")
    length = int.from_bytes(data[4:6], "little")
    if data[6] not in (0, 1):
        raise SiLabsFrameError("SiLabs encryption flag is invalid.")
    if len(data) != 7 + length:
        raise SiLabsFrameError("SiLabs transport length does not match the frame.")
    return SiLabsFrame(
        sequence=packed & 0x0FFFFFFF,
        boot_count=packed >> 28,
        length=length,
        encrypted=bool(data[6]),
        body=data[7:],
    )


def build_outgoing_frame(sequence: int, body: bytes, *, encrypted: bool = False) -> bytes:
    """Build a header for structural fixtures; callers supply already prepared bytes."""

    if not 0 <= sequence <= 0xFFFFFFFF or len(body) > 0xFFFF:
        raise SiLabsFrameError("SiLabs sequence or body length is out of range.")
    return (
        sequence.to_bytes(4, "little")
        + len(body).to_bytes(2, "little")
        + bytes((int(encrypted),))
        + body
    )

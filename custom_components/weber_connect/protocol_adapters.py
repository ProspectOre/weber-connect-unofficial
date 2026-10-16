"""Independent pairing boundaries for the three observed Weber BLE families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .silabs_frames import SiLabsFrame, parse_incoming_frame
from .transport_family import TransportEvidence, TransportFamily


class ProtocolQualificationRequired(RuntimeError):
    """A controller needs additional verified protocol evidence before pairing."""


class TransportAdapter(Protocol):
    """Wire-specific setup contract; cloud runtime starts only after pairing."""

    @property
    def family(self) -> TransportFamily: ...

    def require_pairing_support(self, evidence: TransportEvidence) -> None: ...


@dataclass(frozen=True, slots=True)
class SaberAdapter:
    """The existing tested Saber pairing path."""

    family: TransportFamily = TransportFamily.SABER

    def require_pairing_support(self, evidence: TransportEvidence) -> None:
        if evidence.family is not self.family:
            raise ProtocolQualificationRequired("Saber transport evidence changed before pairing.")


@dataclass(frozen=True, slots=True)
class SiLabsApplianceAdapter:
    """Validated framing boundary; authenticated pairing awaits device evidence."""

    family: TransportFamily = TransportFamily.SILABS_APPLIANCE

    def parse_frame(self, data: bytes) -> SiLabsFrame:
        return parse_incoming_frame(data)

    def require_pairing_support(self, evidence: TransportEvidence) -> None:
        raise ProtocolQualificationRequired(
            "This SiLabs grill has a distinct encrypted pairing protocol. "
            "A sanitized greeting and pairing result plus secure-session validation "
            "are required before Home Assistant can pair it."
        )


@dataclass(frozen=True, slots=True)
class PortableProbeAdapter:
    """Portable GATT boundary; no appliance-frame or cloud pairing assumption."""

    family: TransportFamily = TransportFamily.PORTABLE

    def require_pairing_support(self, evidence: TransportEvidence) -> None:
        raise ProtocolQualificationRequired(
            "This portable probe uses separate GATT characteristics. Its pairing "
            "and authenticated reading contract has not been validated."
        )


@dataclass(frozen=True, slots=True)
class UnqualifiedAdapter:
    """Unknown or conflicting discovery evidence is never sent a command."""

    family: TransportFamily = TransportFamily.UNKNOWN

    def require_pairing_support(self, evidence: TransportEvidence) -> None:
        raise ProtocolQualificationRequired(
            f"Bluetooth controller evidence is {evidence.qualification.value}; "
            "no pairing command was sent."
        )


def adapter_for(evidence: TransportEvidence) -> TransportAdapter:
    """Choose by observed wire family rather than marketing product name."""

    match evidence.family:
        case TransportFamily.SABER:
            return SaberAdapter()
        case TransportFamily.SILABS_APPLIANCE:
            return SiLabsApplianceAdapter()
        case TransportFamily.PORTABLE:
            return PortableProbeAdapter()
        case _:
            return UnqualifiedAdapter()

"""Independent pairing boundaries for the three observed Weber BLE families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .silabs_frames import SiLabsFrame, parse_incoming_frame
from .transport_family import Qualification, TransportEvidence, TransportFamily


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
    """Source-backed authenticated setup; exact hardware acceptance is separate."""

    family: TransportFamily = TransportFamily.SILABS_APPLIANCE

    def parse_frame(self, data: bytes) -> SiLabsFrame:
        return parse_incoming_frame(data)

    def require_pairing_support(self, evidence: TransportEvidence) -> None:
        if (
            evidence.family is not self.family
            or evidence.qualification is not Qualification.NEEDS_SECURE_SESSION
        ):
            raise ProtocolQualificationRequired(
                "SiLabs appliance GATT qualification is incomplete."
            )


@dataclass(frozen=True, slots=True)
class PortableProbeAdapter:
    """Recognize portable probes while preserving the authenticated-data boundary."""

    family: TransportFamily = TransportFamily.PORTABLE

    def require_pairing_support(self, evidence: TransportEvidence) -> None:
        raise ProtocolQualificationRequired(
            "Direct Bluetooth readings from this probe cannot yet be verified. "
            "Use probe readings from a paired Weber Connect controller where available."
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

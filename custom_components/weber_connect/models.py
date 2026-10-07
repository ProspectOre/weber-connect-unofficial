"""Runtime models for the unofficial Weber Connect integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompanionIdentity:
    """Private identity paired with a Weber hub."""

    companion_id: str
    public_key: str


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Result of one physically confirmed pairing operation."""

    message_version: int
    appliance_id: str


@dataclass(frozen=True, slots=True)
class BluetoothFrameSummary:
    """Structural evidence only; never retain packet bytes or identities."""

    received_bytes: int
    transport_present: bool
    transport_length_ok: bool | None
    transport_has_extra: bool
    envelope_present: bool
    envelope_crc_ok: bool | None
    envelope_tail_ok: bool | None


@dataclass(slots=True)
class WeberRuntimeData:
    """Objects owned by one Home Assistant config entry."""

    coordinator: Any

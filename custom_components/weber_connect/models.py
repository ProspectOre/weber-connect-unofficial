"""Runtime models for the unofficial Weber Connect integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompanionIdentity:
    """Private identity paired with a Weber hub."""

    companion_id: str
    private_key: str
    public_key: str


@dataclass(frozen=True, slots=True)
class PairingResult:
    """Result of one physically confirmed pairing operation."""

    message_version: int
    appliance_id: str
    appliance_public_key: str
    verification_code: int | None


@dataclass(slots=True)
class WeberRuntimeData:
    """Objects owned by one Home Assistant config entry."""

    coordinator: Any

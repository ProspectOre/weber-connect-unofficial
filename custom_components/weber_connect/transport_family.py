"""Evidence-based Weber Bluetooth family selection.

Advertisement identity and GATT shape select a protocol family. Catalogue names
alone never authorize a pairing exchange or decoding encrypted telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .saber_frames import APPLIANCE_SERVICE_UUID, COMMAND_UUID, RESPONSE_UUID

JUNE_COMPANY_ID = 0x07C5
SILABS_COMPANY_ID = 0x0DF2
SILABS_OTA_SERVICE_UUID = "1d14d6ee-fd63-4fa1-bfa4-8f47b42119f0"
PORTABLE_SERVICE_UUID = "a9ad69c3-ef84-490c-9749-b93f9165b477"
PORTABLE_TEMPERATURE_UUID = "a0eb972a-6386-4f84-abf2-bcdb9b463908"
LEGACY_MODELS = {
    1: "saber",
    2: "smokefire",
    5: "pulse1000",
    6: "pulse2000",
    7: "spirit",
    8: "genesis",
    16: "smart_probe",
    17: "smart_wireless_probe",
}
PORTABLE_MODEL_IDS = frozenset({16, 17})


class TransportFamily(StrEnum):
    """Wire family, including states that still need controller evidence."""

    SABER = "saber"
    SILABS_UNRESOLVED = "silabs_unresolved"
    SILABS_APPLIANCE = "silabs_appliance"
    PORTABLE = "portable"
    UNKNOWN = "unknown"


class Qualification(StrEnum):
    """What the integration can honestly do with current evidence."""

    PAIRING_READY = "pairing_ready"
    NAME_ONLY = "name_only"
    NEEDS_GATT = "needs_gatt"
    NEEDS_SECURE_SESSION = "needs_secure_session"
    READING_READY = "reading_ready"
    INVALID_ADVERTISEMENT = "invalid_advertisement"
    UNKNOWN_CONTROLLER = "unknown_controller"


@dataclass(frozen=True, slots=True)
class TransportEvidence:
    """Transient classification without serial numbers or packet contents."""

    family: TransportFamily
    qualification: Qualification
    company_id: int | None = None
    schema: int | None = None
    model_id: int | None = None
    model: str | None = None
    sku: str | None = None


def _name_fallback(name: str) -> TransportEvidence:
    normalized = name.casefold().strip()
    if normalized.startswith("smokefire") and (len(normalized) == 9 or not normalized[9].isalpha()):
        return TransportEvidence(TransportFamily.SABER, Qualification.NAME_ONLY, model="smokefire")
    if normalized.startswith("connect hub") and (
        len(normalized) == 11 or not normalized[11].isalpha()
    ):
        return TransportEvidence(TransportFamily.SABER, Qualification.NAME_ONLY, model="saber")
    if normalized.startswith("weber connect") and (
        len(normalized) == 13 or not normalized[13].isalpha()
    ):
        return TransportEvidence(TransportFamily.SABER, Qualification.NAME_ONLY)
    if normalized.startswith("weber hub") and (len(normalized) == 9 or not normalized[9].isalpha()):
        return TransportEvidence(TransportFamily.SABER, Qualification.NAME_ONLY)
    return TransportEvidence(TransportFamily.UNKNOWN, Qualification.UNKNOWN_CONTROLLER)


def classify_advertisement(
    manufacturer_data: dict[int, bytes] | None, name: str = ""
) -> TransportEvidence:
    """Parse the two company schemas without inferring a protocol from product names."""

    data = manufacturer_data or {}
    if JUNE_COMPANY_ID in data and SILABS_COMPANY_ID in data:
        return TransportEvidence(TransportFamily.UNKNOWN, Qualification.INVALID_ADVERTISEMENT)
    if JUNE_COMPANY_ID in data:
        payload = bytes(data[JUNE_COMPANY_ID])
        if len(payload) == 1:
            schema, model_id, sku = None, payload[0], None
        elif len(payload) >= 3:
            schema = payload[0]
            model_id = int.from_bytes(payload[1:3], "little")
            try:
                sku = payload[3:11].strip(b"\x00").decode("utf-8") or None
            except UnicodeDecodeError:
                return TransportEvidence(
                    TransportFamily.UNKNOWN, Qualification.INVALID_ADVERTISEMENT, JUNE_COMPANY_ID
                )
        else:
            return TransportEvidence(
                TransportFamily.UNKNOWN, Qualification.INVALID_ADVERTISEMENT, JUNE_COMPANY_ID
            )
        model = LEGACY_MODELS.get(model_id)
        if model_id not in PORTABLE_MODEL_IDS and name.casefold().startswith("smokefire"):
            model = "smokefire"
        if model is None:
            return TransportEvidence(
                TransportFamily.UNKNOWN,
                Qualification.UNKNOWN_CONTROLLER,
                JUNE_COMPANY_ID,
                schema,
                model_id,
                sku=sku,
            )
        if model_id in PORTABLE_MODEL_IDS:
            return TransportEvidence(
                TransportFamily.PORTABLE,
                Qualification.NEEDS_GATT,
                JUNE_COMPANY_ID,
                schema,
                model_id,
                model,
                sku,
            )
        return TransportEvidence(
            TransportFamily.SABER,
            Qualification.PAIRING_READY,
            JUNE_COMPANY_ID,
            schema,
            model_id,
            model,
            sku,
        )
    if SILABS_COMPANY_ID in data:
        payload = bytes(data[SILABS_COMPANY_ID])
        if len(payload) < 13 or payload[0] > 3:
            return TransportEvidence(
                TransportFamily.UNKNOWN, Qualification.INVALID_ADVERTISEMENT, SILABS_COMPANY_ID
            )
        # Serial occupies bytes 1:9. Deliberately omit it from the result.
        return TransportEvidence(
            TransportFamily.SILABS_UNRESOLVED,
            Qualification.NEEDS_GATT,
            SILABS_COMPANY_ID,
            payload[0],
            sku=payload[9:13].hex().lstrip("0") or "0",
        )
    return _name_fallback(name)


def classify_service_shapes(
    evidence: TransportEvidence,
    services: dict[str, set[str]],
) -> TransportEvidence:
    """Resolve a SiLabs advertisement from read-only GATT service properties."""

    if evidence.qualification is not Qualification.NEEDS_GATT:
        return evidence
    normalized = {uuid.lower(): properties for uuid, properties in services.items()}
    command = normalized.get(COMMAND_UUID)
    response = normalized.get(RESPONSE_UUID)
    appliance = (
        APPLIANCE_SERVICE_UUID in normalized
        and command is not None
        and "write" in command
        and response is not None
        and bool(response & {"read", "notify", "indicate"})
    )
    temperature = normalized.get(PORTABLE_TEMPERATURE_UUID)
    portable = (
        PORTABLE_SERVICE_UUID in normalized
        and temperature is not None
        and bool(temperature & {"read", "notify", "indicate"})
    )
    if (appliance == portable) or (evidence.family is TransportFamily.PORTABLE and appliance):
        return replace(
            evidence, family=TransportFamily.UNKNOWN, qualification=Qualification.UNKNOWN_CONTROLLER
        )
    return replace(
        evidence,
        family=TransportFamily.SILABS_APPLIANCE if appliance else TransportFamily.PORTABLE,
        qualification=Qualification.NEEDS_SECURE_SESSION
        if appliance
        else Qualification.READING_READY,
    )


def classify_discovery(info: Any) -> TransportEvidence:
    """Use the same classification for automatic and manual Home Assistant setup."""

    return classify_advertisement(
        getattr(info, "manufacturer_data", None), str(getattr(info, "name", "") or "")
    )

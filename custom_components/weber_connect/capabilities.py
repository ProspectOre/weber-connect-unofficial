"""Authenticated appliance capabilities shared across product names."""

from __future__ import annotations

from .saber_frames import parse_tlv

# ApplianceCapabilitiesTLV_TLVKt in Android 2.12.0.2665. Deliberately omit
# serial/SKU strings and actuator specifications from the persisted summary.
_FIELDS = {
    1: ("probe_count", 1),
    2: ("burner_count", 1),
    6: ("region", 1),
    8: ("control_interfaces", 8),
    9: ("sensor_interfaces", 8),
    12: ("device_class", 1),
    21: ("capability_bits", 8),
    22: ("stop_cooking_duration_ms", 4),
    23: ("fuel_type", 1),
    24: ("max_wireless_probes", 1),
}


def parse_capabilities(payload: bytes) -> dict[str, int]:
    """Decode bounded, complete TLV records without assuming product support."""

    fields = parse_tlv(payload)
    if -1 in fields:
        raise ValueError("Appliance capabilities contain incomplete or excessive TLV records.")
    result: dict[str, int] = {}
    for tag, (name, width) in _FIELDS.items():
        values = fields.get(tag)
        if values is None:
            continue
        if len(values) != 1 or len(values[0]) != width:
            raise ValueError("Appliance capabilities contain an invalid scalar field.")
        result[name] = int.from_bytes(values[0], "little")
    return result

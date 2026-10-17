"""Offline portable-probe decoding; unauthenticated GATT telemetry stays disabled."""

from __future__ import annotations

from typing import Any

MAX_PORTABLE_SENSORS = 16


def parse_portable_reading(data: bytes, model_id: int) -> dict[str, Any]:
    """Parse the app's packed header, battery and signed deci-C sensor array.

    WASP (16) uses the last ambient sensor; BATWOMAN (17) uses the coldest
    non-ambient segment as its core reading. Missing sensors retain their slot.
    This format provides no application-level authentication evidence.
    """

    if model_id not in (16, 17) or len(data) < 6:
        raise ValueError("Portable probe model or header is invalid.")
    count = data[5]
    if count > MAX_PORTABLE_SENSORS or len(data) != 6 + count * 2:
        raise ValueError("Portable sensor count does not match the complete reading.")
    values = [
        int.from_bytes(data[i : i + 2], "little", signed=True) for i in range(6, len(data), 2)
    ]
    temperatures = [None if value == -32768 else value / 10.0 for value in values]
    ambient = temperatures[-1] if temperatures else None
    segments = [value for value in temperatures[:-1] if value is not None]
    core = min(segments) if segments else None
    packed = int.from_bytes(data[:4], "little")
    return {
        "sequence": packed & 0x0FFFFFFF,
        "boot_count": packed >> 28,
        "sensor_count": count,
        "battery_level": data[4] if data[4] <= 100 else None,
        "device_state": "on",
        "probes": [
            {
                "probe_number": 1,
                "probe_temp_c": ambient if model_id == 16 else core,
                "ambient_temp_c": ambient,
                "battery_level": data[4] if data[4] <= 100 else None,
                "probe_type": "ambient" if model_id == 16 else "wireless",
            }
        ],
    }

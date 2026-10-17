"""Read-only portable-probe protocol, GATT, and integration contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import ConfigEntryError

from custom_components.weber_connect import coordinator as coordinator_module
from custom_components.weber_connect import portable
from custom_components.weber_connect.const import CONF_TRANSPORT_FAMILY
from custom_components.weber_connect.coordinator import WeberCoordinator


def reading(
    *deci_c_values: int,
    model_sequence: int = (3 << 28) | 0x01234567,
    battery: int = 88,
) -> bytes:
    return (
        model_sequence.to_bytes(4, "little")
        + bytes((battery, len(deci_c_values)))
        + b"".join(value.to_bytes(2, "little", signed=True) for value in deci_c_values)
    )


def test_portable_parser_decodes_header_sentinel_and_signed_deci_c() -> None:
    parsed = portable.parse_portable_reading(
        reading(-32768, -123, 254, model_sequence=(5 << 28) | 0x0ABCDEF), 16
    )

    assert parsed["sequence"] == 0x0ABCDEF
    assert parsed["boot_count"] == 5
    assert parsed["sensor_count"] == 3
    assert parsed["battery_level"] == 88
    assert parsed["device_state"] == "on"
    assert parsed["probes"] == [
        {
            "probe_number": 1,
            "probe_temp_c": 25.4,
            "ambient_temp_c": 25.4,
            "battery_level": 88,
            "probe_type": "ambient",
        }
    ]


def test_wasp_uses_ambient_and_batwoman_uses_coldest_core_sensor() -> None:
    payload = reading(380, -123, 210)

    wasp = portable.parse_portable_reading(payload, 16)["probes"][0]
    batwoman = portable.parse_portable_reading(payload, 17)["probes"][0]

    assert wasp["probe_temp_c"] == 21.0
    assert wasp["probe_type"] == "ambient"
    assert batwoman["probe_temp_c"] == -12.3
    assert batwoman["ambient_temp_c"] == 21.0
    assert batwoman["probe_type"] == "wireless"


def test_portable_parser_preserves_missing_ambient_sensor_and_rejects_bad_battery() -> None:
    parsed = portable.parse_portable_reading(reading(330, -32768, battery=101), 17)

    assert parsed["battery_level"] is None
    assert parsed["probes"][0]["probe_temp_c"] == 33.0
    assert parsed["probes"][0]["ambient_temp_c"] is None
    assert parsed["probes"][0]["battery_level"] is None


@pytest.mark.parametrize("model_id", [0, 15, 18, 999])
def test_portable_parser_rejects_unknown_model(model_id: int) -> None:
    with pytest.raises(ValueError, match="model or header"):
        portable.parse_portable_reading(reading(200), model_id)


@pytest.mark.parametrize("payload", [b"", b"12345", reading(200)[:-1], reading(*([200] * 17))])
def test_portable_parser_rejects_short_or_inconsistent_sensor_count(payload: bytes) -> None:
    with pytest.raises(ValueError, match="Portable"):
        portable.parse_portable_reading(payload, 16)


@pytest.mark.usefixtures("enable_custom_integrations")
def test_unauthenticated_portable_entry_cannot_start_live_transport(hass: object) -> None:
    entry = SimpleNamespace(data={CONF_TRANSPORT_FAMILY: "portable"}, options={})
    with (
        patch.object(coordinator_module, "WeberCloudClient") as cloud,
        pytest.raises(ConfigEntryError, match="verified authentication"),
    ):
        WeberCoordinator(hass, entry)  # type: ignore[arg-type]
    cloud.assert_not_called()

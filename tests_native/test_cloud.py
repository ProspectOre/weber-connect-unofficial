"""Cloud coexistence contracts for the generated Weber companion."""

from __future__ import annotations

import pytest

from custom_components.weber_connect import weber_cloud as cloud

DEVICE_ID = "11" * 16
APPLIANCE_ID = "22" * 16


def _config(**updates: object) -> cloud.CloudConfig:
    values: dict[str, object] = {
        "device_id": DEVICE_ID,
        "device_password": "private-password",
        "temperature_unit": "deci_celsius",
        "identity_source": "home_assistant",
    }
    values.update(updates)
    return cloud.CloudConfig.from_mapping(values)


def test_cloud_config_redacts_password_and_validates_ids() -> None:
    config = _config(appliance_id=APPLIANCE_ID)
    assert config.as_dict()["device_password"] == "private-password"
    assert "device_password" not in config.public_dict()
    assert config.public_dict()["device_id_suffix"] == DEVICE_ID[-6:]
    with pytest.raises(ValueError):
        _config(device_id="invalid")
    with pytest.raises(ValueError):
        _config(device_password="")


def test_generated_cloud_identity_reuses_valid_companion_id() -> None:
    generated = cloud.CloudConfig.generate(DEVICE_ID)
    assert generated.device_id == DEVICE_ID
    assert generated.identity_source == "home_assistant"
    assert generated.temperature_unit == "deci_celsius"
    assert len(generated.device_password) == 32


@pytest.mark.parametrize(
    ("raw", "unit", "expected"),
    [
        (250, "deci_celsius", (77.0, 25.0)),
        (25, "celsius", (77.0, 25.0)),
        (77, "fahrenheit", (77.0, 25.0)),
    ],
)
def test_cloud_temperature_normalization(
    raw: int, unit: str, expected: tuple[float, float]
) -> None:
    assert cloud.normalize_cloud_temperature(raw, unit) == expected


def test_snapshot_normalization_keeps_probe_cavity_and_timer_slots() -> None:
    state = cloud.cloud_status_from_snapshot(
        {
            "snapshot_id": 9,
            "data": {
                "probe_status": [{"index": 1, "temperature": 250}],
                "cavity_status": [{"index": 0, "temperature": 1210}],
                "timer_status": [{"index": 2, "duration": 90_000}],
            },
        },
        "deci_celsius",
    )
    assert state["probes"][0]["probe_number"] == 2
    assert state["probes"][0]["probe_temp_c"] == 25.0
    assert state["cavities"][0]["temperature_c"] == 121.0
    assert state["timers"][0]["remaining_s"] == 90


def test_appliance_resolution_never_selects_an_ambiguous_account() -> None:
    assert cloud.resolve_associated_appliance_id([{"oven_id": APPLIANCE_ID}]) == APPLIANCE_ID
    assert (
        cloud.resolve_associated_appliance_id([{"oven_id": APPLIANCE_ID}, {"oven_id": "33" * 16}])
        is None
    )
    assert (
        cloud.resolve_associated_appliance_id(
            [{"oven_id": APPLIANCE_ID}, {"oven_id": "33" * 16}],
            APPLIANCE_ID,
        )
        == APPLIANCE_ID
    )

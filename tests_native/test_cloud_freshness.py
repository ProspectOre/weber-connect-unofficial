"""RC4 field-report regressions for fuel freshness and stale cloud status.

The reported symptoms and counters are from PR #71, comment 6257147619.
The routed messages below are synthetic; no captured packets or identifiers
are included, and software reproduction does not establish hardware acceptance.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.weber_connect import weber_cloud_socket as socket
from custom_components.weber_connect.binary_sensor import WeberConnectionBinarySensor
from custom_components.weber_connect.diagnostics import async_get_config_entry_diagnostics
from custom_components.weber_connect.models import WeberRuntimeData
from custom_components.weber_connect.sensor import (
    CLOUD_STATUS_SENSOR,
    FUEL_LEVEL_SENSOR,
    FUEL_PERCENT_SENSOR,
    WeberSensor,
)
from tests_native.test_cloud_socket import (
    APPLIANCE_ID,
    FakeCloudClient,
    FakeConnection,
    FakeHass,
    routed,
    tlv,
)
from tests_native.test_coordinator import _coordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("failure", [ConnectionResetError("closed"), TimeoutError()])
async def test_fast_reconnect_keeps_recent_fuel_without_synthetic_unknowns(hass, failure):
    first = FakeConnection(
        [routed(0x83, tlv(20, b"\x01") + tlv(27, b"\x64")), routed(0x80), failure]
    )
    second = FakeConnection(
        [routed(0x80), routed(0x83, tlv(20, b"\x05") + tlv(27, b"\x12")), routed(0x80)]
    )
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, timeout=0.1, subscribe_delay=0
    )
    coordinator, _ = _coordinator(hass, cloud=True)
    level = WeberSensor(coordinator, coordinator.entry, FUEL_LEVEL_SENSOR)
    percent = WeberSensor(coordinator, coordinator.entry, FUEL_PERCENT_SENSOR)
    observed = []
    with patch.object(socket, "connect", AsyncMock(side_effect=[first, second])):
        for _ in range(3):
            coordinator._async_status(await session.async_request_status())
            observed.append((level.native_value, percent.native_value))
    assert observed == [("full", 100), ("full", 100), ("low", 18)]
    assert session.fuel_level_unknown_reports == 0
    assert session.fuel_level_changes == 1
    assert session.fast_recoveries == 1
    assert coordinator.failed_updates == 0
    await session.async_close()


@pytest.mark.parametrize(
    "intervening", [b"", tlv(1, b"\x40"), tlv(20, b"\x00\x01"), tlv(27, b"\x65")]
)
async def test_missing_partial_or_invalid_fuel_does_not_extend_freshness(intervening):
    clock = SimpleNamespace(now=100.0)
    connection = FakeConnection(
        [
            routed(0x83, tlv(20, b"\x01") + tlv(27, b"\x64")),
            routed(0x80),
            routed(0x83, intervening),
            routed(0x80),
            routed(0x80),
            routed(0x83, tlv(20, b"\x05")),
            routed(0x80),
        ]
    )
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, timeout=0.1, subscribe_delay=0
    )
    with (
        patch.object(socket, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock.now)),
        patch.object(socket, "connect", AsyncMock(return_value=connection)),
    ):
        assert (await session.async_request_status())["fuel_level"] == "full"
        clock.now = 159.0
        assert (await session.async_request_status())["fuel_level"] == "full"
        clock.now = 160.0
        expired = await session.async_request_status()
        assert "fuel_level" not in expired
        assert "fuel_percent" not in expired
        recovered = await session.async_request_status()
        assert recovered["fuel_level"] == "low"
        assert "fuel_percent" not in recovered
    await session.async_close()


async def test_long_reconnect_does_not_restore_old_fuel_or_cloud_status():
    clock = SimpleNamespace(now=100.0)
    first = FakeConnection([routed(0x83, tlv(20, b"\x01") + tlv(4, b"\x03")), routed(0x80)])
    second = FakeConnection([routed(0x80)])
    client = FakeCloudClient()
    session = socket.WeberCloudSession(
        FakeHass(), client, APPLIANCE_ID, timeout=0.1, subscribe_delay=0
    )
    with (
        patch.object(socket, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock.now)),
        patch.object(socket, "connect", AsyncMock(side_effect=[first, second])),
    ):
        await session.async_request_status()
        clock.now = 160.0
        client.refresh_required = True
        resumed = await session.async_request_status()
        assert "fuel_level" not in resumed
        assert "cloud_connection_status" not in resumed
    await session.async_close()


async def test_fuel_fields_expire_independently_and_explicit_unknown_is_immediate():
    clock = SimpleNamespace(now=100.0)
    connection = FakeConnection(
        [
            routed(0x83, tlv(20, b"\x01") + tlv(27, b"\x64")),
            routed(0x80),
            routed(0x83, tlv(27, b"\x32")),
            routed(0x80),
            routed(0x83, tlv(27, b"\x00\x64")),
            routed(0x80),
            routed(0x83, tlv(20, b"\x00")),
            routed(0x80),
        ]
    )
    session = socket.WeberCloudSession(
        FakeHass(), FakeCloudClient(), APPLIANCE_ID, timeout=0.1, subscribe_delay=0
    )
    with (
        patch.object(socket, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock.now)),
        patch.object(socket, "connect", AsyncMock(return_value=connection)),
    ):
        await session.async_request_status()
        clock.now = 140.0
        await session.async_request_status()
        clock.now = 160.0
        expired_level = await session.async_request_status()
        assert "fuel_level" not in expired_level
        assert expired_level["fuel_percent"] == 50
        explicit_unknown = await session.async_request_status()
        assert explicit_unknown["fuel_level"] == "unknown"
        assert session.fuel_level_unknown_reports == 1
    await session.async_close()


@pytest.mark.parametrize("failures", [1, 3, 20])
async def test_stale_cloud_status_is_unknown_in_entities_and_diagnostics(hass, failures):
    coordinator, _ = _coordinator(hass, cloud=True)
    coordinator._async_status({"cloud_connection_status": "connected", "fuel_level": "full"})
    cloud = WeberSensor(coordinator, coordinator.entry, CLOUD_STATUS_SENSOR)
    fuel = WeberSensor(coordinator, coordinator.entry, FUEL_LEVEL_SENSOR)
    connection = WeberConnectionBinarySensor(coordinator, coordinator.entry)
    identity = cloud.unique_id
    assert cloud.native_value == "connected"
    for _ in range(failures):
        coordinator._async_error("timeout")
    assert cloud.native_value is None
    assert cloud.extra_state_attributes["last_reported_status"] == "connected"
    assert cloud.extra_state_attributes["last_successful_update"] is not None
    assert connection.is_on is (failures < 3)
    assert fuel.native_value == ("full" if failures < 3 else None)
    coordinator.entry.runtime_data = WeberRuntimeData(coordinator=coordinator)
    diagnostics = await async_get_config_entry_diagnostics(hass, coordinator.entry)
    assert diagnostics["cloud_connection_status"] is None
    assert diagnostics["last_reported_cloud_connection_status"] == "connected"
    # A fresh cook-only response must not resurrect the stale device report.
    coordinator._async_status({"probes": []})
    assert cloud.native_value is None
    assert cloud.extra_state_attributes["last_reported_status"] == "connected"
    coordinator._async_status({"cloud_connection_status": "connected"})
    assert cloud.native_value == "connected"
    assert cloud.unique_id == identity

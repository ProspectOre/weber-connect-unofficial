"""Read-only portable probe GATT transport, separate from appliance pairing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache
from homeassistant.core import HomeAssistant

from .bluetooth import GattResponseChannel, WeberBluetoothError, _connect, _safe_disconnect
from .transport_family import PORTABLE_TEMPERATURE_UUID

MAX_PORTABLE_SENSORS = 16
READING_TIMEOUT = 30.0
RECONNECT_DELAY = 5.0


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


async def async_read_portable(hass: HomeAssistant, address: str, model_id: int) -> dict[str, Any]:
    """Validate one local reading for setup without commands or cloud registration."""

    client: BleakClientWithServiceCache | None = None
    try:
        client = await _connect(hass, address, use_services_cache=False)
        channel = GattResponseChannel(client, PORTABLE_TEMPERATURE_UUID)
        await channel.async_open()
        async with asyncio.timeout(READING_TIMEOUT):
            return parse_portable_reading(await channel.async_receive(), model_id)
    except (BleakError, TimeoutError, ValueError) as exc:
        raise WeberBluetoothError("The portable probe did not provide a complete reading.") from exc
    finally:
        if client is not None:
            await _safe_disconnect(client)


class PortableSession:
    """One bounded, cancellable HA-routed BLE read/notify session per probe."""

    def __init__(self, hass: HomeAssistant, address: str, model_id: int) -> None:
        self.hass = hass
        self.address = address
        self.model_id = model_id
        self._wake = asyncio.Event()
        self._closed = False
        self._client: BleakClientWithServiceCache | None = None

    async def async_run(
        self,
        status_callback: Callable[[dict[str, Any]], None],
        error_callback: Callable[[str], None],
    ) -> None:
        while not self._closed:
            try:
                self._client = await _connect(self.hass, self.address, use_services_cache=False)
                channel = GattResponseChannel(self._client, PORTABLE_TEMPERATURE_UUID)
                await channel.async_open()
                while not self._closed:
                    async with asyncio.timeout(READING_TIMEOUT):
                        data = await channel.async_receive()
                    status_callback(parse_portable_reading(data, self.model_id))
            except BleakError, WeberBluetoothError, TimeoutError, ValueError:
                error_callback("The portable Bluetooth reading is unavailable.")
            finally:
                if self._client is not None:
                    await _safe_disconnect(self._client)
                    self._client = None
            if not self._closed:
                try:
                    async with asyncio.timeout(RECONNECT_DELAY):
                        await self._wake.wait()
                except TimeoutError:
                    pass
                self._wake.clear()

    def async_wake(self) -> None:
        self._wake.set()

    async def async_close(self) -> None:
        self._closed = True
        self._wake.set()

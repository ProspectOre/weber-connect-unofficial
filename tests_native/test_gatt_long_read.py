"""Targeted GATT reads after SiLabs long-response notifications."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bleak.exc import BleakError

from custom_components.weber_connect.bluetooth import GattResponseChannel, WeberBluetoothError
from custom_components.weber_connect.saber_frames import COMMAND_UUID
from custom_components.weber_connect.silabs_frames import build_outgoing_frame


class ReadClient:
    def __init__(self, properties: set[str], response: bytes) -> None:
        self.response_properties = properties
        self.services = SimpleNamespace(get_characteristic=self.get_characteristic)
        self.read_gatt_char = AsyncMock(return_value=response)
        self.start_notify = AsyncMock()
        self.write_gatt_char = AsyncMock()

    def get_characteristic(self, uuid: str) -> SimpleNamespace:
        properties = {"write"} if uuid == COMMAND_UUID else self.response_properties
        return SimpleNamespace(properties=properties)


async def test_complete_notified_frame_never_causes_a_read() -> None:
    frame = build_outgoing_frame(1, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, frame)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(frame)

    assert await channel.async_receive() == frame
    client.read_gatt_char.assert_not_awaited()


async def test_truncated_notification_reads_matching_complete_response() -> None:
    frame = build_outgoing_frame(1, b"\x0b\x85" + bytes(81))
    notification = frame[:83]
    client = ReadClient({"read", "notify"}, frame)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(notification)

    assert await channel.async_receive() == frame
    client.read_gatt_char.assert_awaited_once_with("response")


async def test_empty_long_read_notification_skips_the_previous_complete_response() -> None:
    frame = build_outgoing_frame(7, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, frame)
    previous = build_outgoing_frame(6, b"\x0b\x81")
    client.read_gatt_char = AsyncMock(side_effect=[previous, frame])
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(previous)
    assert await channel.async_receive() == previous
    channel._queue.put_nowait(b"")

    async with asyncio.timeout(2):
        assert await channel.async_receive() == frame
    assert client.read_gatt_char.await_count == 2


async def test_long_read_retries_after_a_mismatched_frame() -> None:
    notified = build_outgoing_frame(1, b"\x0b\x85" + bytes(81))
    stale = build_outgoing_frame(2, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, notified)
    client.read_gatt_char = AsyncMock(side_effect=[stale, notified])
    channel = GattResponseChannel(client, "response")
    await channel.async_open()

    async with asyncio.timeout(1):
        assert await channel._notification_data(notified[:83]) == notified
    assert client.read_gatt_char.await_count == 2


async def test_long_read_is_not_attempted_when_characteristic_is_notify_only() -> None:
    frame = build_outgoing_frame(1, b"\x0b\x85" + bytes(81))
    client = ReadClient({"notify"}, frame)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()

    assert await channel._notification_data(frame[:83]) == b""
    client.read_gatt_char.assert_not_awaited()


async def test_incomplete_long_read_retries_after_failures_until_complete() -> None:
    frame = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, frame)
    client.read_gatt_char = AsyncMock(side_effect=[BleakError("busy"), frame[:83], frame])
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(frame[:83])

    async with asyncio.timeout(2):
        assert await channel.async_receive() == frame
    assert client.read_gatt_char.await_count == 3


async def test_complete_superseding_notification_interrupts_long_read_retry() -> None:
    partial = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    complete = build_outgoing_frame(29, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, partial)
    read_started = asyncio.Event()

    async def slow_read(_uuid: str) -> bytes:
        read_started.set()
        await asyncio.Event().wait()
        return partial

    client.read_gatt_char = AsyncMock(side_effect=slow_read)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(partial[:83])
    receive = asyncio.create_task(channel.async_receive())
    await read_started.wait()
    channel._queue.put_nowait(complete)

    assert await asyncio.wait_for(receive, 1) == complete
    client.read_gatt_char.assert_awaited_once_with("response")


async def test_superseding_truncated_notification_changes_read_prefix() -> None:
    initial = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    superseding = build_outgoing_frame(29, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, superseding)
    first_read_started = asyncio.Event()
    first_read_cancelled = asyncio.Event()
    read_count = 0

    async def read_response(_uuid: str) -> bytes:
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            first_read_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                first_read_cancelled.set()
        return superseding

    client.read_gatt_char = AsyncMock(side_effect=read_response)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(initial[:83])
    receive = asyncio.create_task(channel.async_receive())
    await first_read_started.wait()
    channel._queue.put_nowait(superseding[:83])

    assert await asyncio.wait_for(receive, 1) == superseding
    assert first_read_cancelled.is_set()
    assert read_count == 2


async def test_disconnect_interrupts_long_read_retry() -> None:
    partial = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, partial)
    read_started = asyncio.Event()

    async def slow_read(_uuid: str) -> bytes:
        read_started.set()
        await asyncio.Event().wait()
        return partial

    client.read_gatt_char = AsyncMock(side_effect=slow_read)
    link_lost = asyncio.Event()
    channel = GattResponseChannel(client, "response", link_lost)
    await channel.async_open()
    channel._queue.put_nowait(partial[:83])
    receive = asyncio.create_task(channel.async_receive())
    await read_started.wait()
    link_lost.set()

    with pytest.raises(WeberBluetoothError, match="closed the Bluetooth connection"):
        await asyncio.wait_for(receive, 1)


async def test_deadline_cancels_long_read_and_notification_waiters() -> None:
    partial = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, partial)
    read_started = asyncio.Event()
    read_cancelled = asyncio.Event()

    async def slow_read(_uuid: str) -> bytes:
        read_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            read_cancelled.set()
        return partial

    client.read_gatt_char = AsyncMock(side_effect=slow_read)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(partial[:83])

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await channel.async_receive()
    assert read_started.is_set()
    assert read_cancelled.is_set()


async def test_polling_pairing_ignores_preexisting_characteristic_value() -> None:
    stale = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    fresh = build_outgoing_frame(29, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read"}, fresh)
    client.read_gatt_char = AsyncMock(side_effect=[stale, stale, fresh])
    channel = GattResponseChannel(client, "response")
    await channel.async_open()

    await channel.async_write(build_outgoing_frame(1, b"\x0b\x0a"))
    assert await channel.async_receive() == fresh
    assert client.read_gatt_char.await_count == 3
    client.write_gatt_char.assert_awaited_once()


async def test_pairing_drains_pre_request_notifications_on_notified_route() -> None:
    frame = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, frame)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(frame)

    await channel.async_write(build_outgoing_frame(1, b"\x0b\x0a"))

    assert channel._queue.empty()
    client.read_gatt_char.assert_not_awaited()


async def test_polling_pairing_accepts_empty_baseline_but_fails_on_read_error() -> None:
    client = ReadClient({"read"}, b"")
    client.read_gatt_char = AsyncMock(side_effect=[BleakError("busy"), b""])
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    await channel.async_write(build_outgoing_frame(1, b"\x0b\x0a"))
    client.write_gatt_char.assert_awaited_once()
    assert client.read_gatt_char.await_count == 2

    client = ReadClient({"read"}, b"")
    client.read_gatt_char = AsyncMock(side_effect=BleakError("unavailable"))
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    with pytest.raises(WeberBluetoothError, match="checked before requesting approval"):
        await channel.async_write(build_outgoing_frame(1, b"\x0b\x0a"))
    client.write_gatt_char.assert_not_awaited()

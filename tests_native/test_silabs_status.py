"""Optional SiLabs STATUS notifications are bounded and structurally filtered."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.weber_connect import bluetooth
from custom_components.weber_connect.silabs_frames import build_outgoing_frame

COMPANION_ID = bytes(range(16))


class StatusClient:
    def __init__(self) -> None:
        self.response = SimpleNamespace(properties={"read", "notify"})
        self.status = SimpleNamespace(properties={"notify"})
        self.command = SimpleNamespace(properties={"write"})
        self.services = SimpleNamespace(get_characteristic=self.characteristic)
        self.callbacks = {}
        self.start_notify = AsyncMock(side_effect=self.subscribe)
        self.write_gatt_char = AsyncMock()
        self.read_gatt_char = AsyncMock(return_value=b"")

    def characteristic(self, uuid: str) -> object | None:
        return {
            bluetooth.RESPONSE_UUID: self.response,
            bluetooth.STATUS_UUID: self.status,
            bluetooth.COMMAND_UUID: self.command,
        }.get(uuid)

    async def subscribe(self, uuid: str, callback: object) -> None:
        self.callbacks[uuid] = callback


@pytest.fixture
async def opened_status_channel() -> tuple[StatusClient, bluetooth.GattResponseChannel]:
    client = StatusClient()
    channel = bluetooth.GattResponseChannel(client, bluetooth.RESPONSE_UUID)
    await channel.async_open()
    greeting = build_outgoing_frame(2, b"\x0b\x70" + COMPANION_ID)
    await channel.async_write(greeting)
    return client, channel


@pytest.mark.parametrize(
    "notification",
    [
        b"\x00",
        b"\x00\x00\x00\x00\x00\x00\x02",
        b"\x00\x00\x00\x00\x01\x00\x02",
    ],
)
async def test_status_ignores_malformed_notifications(
    opened_status_channel: tuple[StatusClient, bluetooth.GattResponseChannel],
    notification: bytes,
) -> None:
    client, channel = opened_status_channel
    callback = client.callbacks[bluetooth.STATUS_UUID]
    callback(None, bytearray(notification))
    valid_ack = b"\xf2"
    callback(None, bytearray(valid_ack))
    assert await channel.async_receive() == valid_ack


@pytest.mark.parametrize(
    ("notification", "response"),
    [
        (b"", build_outgoing_frame(28, bytes(28), encrypted=True)),
        (
            build_outgoing_frame(28, bytes(28), encrypted=True)[:9],
            build_outgoing_frame(28, bytes(28), encrypted=True),
        ),
    ],
)
async def test_status_long_read_signal_reads_response_characteristic(
    opened_status_channel: tuple[StatusClient, bluetooth.GattResponseChannel],
    notification: bytes,
    response: bytes,
) -> None:
    client, channel = opened_status_channel
    client.read_gatt_char = AsyncMock(return_value=response)
    callback = client.callbacks[bluetooth.STATUS_UUID]
    callback(None, bytearray(notification))
    assert await asyncio.wait_for(channel.async_receive(), timeout=1) == response
    client.read_gatt_char.assert_awaited_with(bluetooth.RESPONSE_UUID)


async def test_nonmatching_long_read_does_not_obstruct_later_status_ack(
    opened_status_channel: tuple[StatusClient, bluetooth.GattResponseChannel],
) -> None:
    client, channel = opened_status_channel
    expected = build_outgoing_frame(28, bytes(28), encrypted=True)
    signal = expected[:9]
    different = build_outgoing_frame(29, bytes(28), encrypted=True)
    retrying = asyncio.Event()
    read_count = 0

    async def read_response(_uuid: str) -> bytes:
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return different
        retrying.set()
        await asyncio.Event().wait()
        return b""

    client.read_gatt_char = read_response
    callback = client.callbacks[bluetooth.STATUS_UUID]
    receive = asyncio.create_task(channel.async_receive())
    callback(None, bytearray(signal))
    await asyncio.wait_for(retrying.wait(), timeout=1)
    callback(None, bytearray(b"\xf2"))
    assert await asyncio.wait_for(receive, timeout=1) == b"\xf2"
    assert read_count >= 2


@pytest.mark.parametrize(
    "notification",
    [
        b"\xf2",
        build_outgoing_frame(28, b"\x0b\xf2"),
        build_outgoing_frame(29, b"\x0b\xf2", encrypted=True),
    ],
)
async def test_status_preserves_complete_greeting_frames(
    opened_status_channel: tuple[StatusClient, bluetooth.GattResponseChannel],
    notification: bytes,
) -> None:
    client, channel = opened_status_channel
    callback = client.callbacks[bluetooth.STATUS_UUID]
    callback(None, bytearray(notification))
    assert await channel.async_receive() == notification


async def test_status_ignores_notifications_until_greeting_is_sent() -> None:
    client = StatusClient()
    channel = bluetooth.GattResponseChannel(client, bluetooth.RESPONSE_UUID)
    await channel.async_open()
    callback = client.callbacks[bluetooth.STATUS_UUID]
    callback(None, bytearray(b"\xf2"))
    assert channel._queue.empty()

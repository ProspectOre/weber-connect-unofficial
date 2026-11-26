"""Targeted GATT reads after SiLabs long-response notifications."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.weber_connect.bluetooth import GattResponseChannel
from custom_components.weber_connect.silabs_frames import build_outgoing_frame


class ReadClient:
    def __init__(self, properties: set[str], response: bytes) -> None:
        self.services = SimpleNamespace(
            get_characteristic=lambda _uuid: SimpleNamespace(properties=properties)
        )
        self.read_gatt_char = AsyncMock(return_value=response)
        self.start_notify = AsyncMock()


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


async def test_empty_long_read_notification_reads_one_complete_response() -> None:
    frame = build_outgoing_frame(7, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, frame)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(b"")

    assert await channel.async_receive() == frame
    client.read_gatt_char.assert_awaited_once_with("response")


async def test_long_read_rejects_a_different_frame_than_its_truncation() -> None:
    notified = build_outgoing_frame(1, b"\x0b\x85" + bytes(81))
    stale = build_outgoing_frame(1, b"\x0b\x85" + bytes([1]) * 81)
    client = ReadClient({"read", "notify"}, stale)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()

    assert await channel._notification_data(notified[:83]) == b""
    client.read_gatt_char.assert_awaited_once_with("response")


async def test_long_read_is_not_attempted_when_characteristic_is_notify_only() -> None:
    frame = build_outgoing_frame(1, b"\x0b\x85" + bytes(81))
    client = ReadClient({"notify"}, frame)
    channel = GattResponseChannel(client, "response")
    await channel.async_open()

    assert await channel._notification_data(frame[:83]) == b""
    client.read_gatt_char.assert_not_awaited()


async def test_incomplete_long_read_waits_for_a_new_notification() -> None:
    frame = build_outgoing_frame(28, b"\x0b\x85" + bytes(81))
    client = ReadClient({"read", "notify"}, frame[:83])
    channel = GattResponseChannel(client, "response")
    await channel.async_open()
    channel._queue.put_nowait(frame[:83])
    channel._queue.put_nowait(frame)

    assert await channel.async_receive() == frame
    client.read_gatt_char.assert_awaited_once_with("response")

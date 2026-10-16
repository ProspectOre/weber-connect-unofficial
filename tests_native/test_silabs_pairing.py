"""Simulated real-crypto SiLabs lifecycle; no hardware or cloud is contacted."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.exc import BleakError

from custom_components.weber_connect import bluetooth as ble
from custom_components.weber_connect import silabs_pairing as pairing
from custom_components.weber_connect.models import CompanionIdentity
from custom_components.weber_connect.silabs_frames import build_outgoing_frame, parse_incoming_frame
from custom_components.weber_connect.silabs_secure import (
    ExchangeState,
    SiLabsCipher,
    SiLabsExchange,
    SiLabsSecurityError,
    SiLabsSigningKeys,
)

COMPANION_ID = bytes(range(16))
APPLIANCE_ID = bytes(range(16, 32))


class Peer:
    """An offline peer checks actual frame and GCM bytes in both directions."""

    def __init__(self, status: int = 0) -> None:
        self.keys = SiLabsSigningKeys.generate()
        self.replies: asyncio.Queue[bytes] = asyncio.Queue()
        self.writes: list[bytes] = []
        self.cipher: SiLabsCipher | None = None
        self.status = status
        self.capabilities = b"\x01\x01\x04\x18\x01\x02"
        self.silent = False

    async def async_write(self, raw: bytes) -> None:
        self.writes.append(raw)
        if self.silent:
            return
        frame = parse_incoming_frame(raw)
        if frame.encrypted:
            assert self.cipher is not None
            assert self.cipher.open(frame.body) == b"\x0b\x0e"
            body = b"\x0b\x88" + self.capabilities
            await self.replies.put(
                build_outgoing_frame(frame.sequence, self.cipher.seal(body), encrypted=True)
            )
        elif frame.body[1] == 0x0A:
            assert frame.body[2:18] == COMPANION_ID
            self.cipher = SiLabsCipher(self.keys, frame.body[18:82])
            body = b"\x0b\x85" + APPLIANCE_ID + self.keys.public_key + bytes((self.status,))
            await self.replies.put(build_outgoing_frame(frame.sequence, body))
        else:
            assert frame.body == b"\x0b\x70" + COMPANION_ID
            assert self.cipher is not None
            await self.replies.put(
                build_outgoing_frame(frame.sequence, self.cipher.seal(b"\x0b\xf2"), encrypted=True)
            )

    async def async_receive(self) -> bytes:
        return await self.replies.get()


async def test_pair_store_reload_and_authenticated_resume_without_pairing_request() -> None:
    keys = SiLabsSigningKeys.generate()
    peer = Peer()
    exchange = SiLabsExchange(COMPANION_ID, "HA", keys)
    progress = Mock()
    result = await pairing.async_pair_exchange(peer, exchange, progress_callback=progress)
    assert result.appliance_id == APPLIANCE_ID.hex()
    assert result.capabilities == {"probe_count": 4, "max_wireless_probes": 2}
    assert exchange.state is ExchangeState.ACTIVE
    assert progress.call_count == 2
    saved = result.credentials
    restored_keys = SiLabsSigningKeys.from_private_scalar(saved.keys.private_scalar)
    restored = type(saved)(
        saved.companion_id,
        saved.appliance_id,
        restored_keys,
        saved.peer_public_key,
        saved.message_version,
    )
    peer.writes.clear()
    capabilities = await pairing.async_resume_exchange(peer, restored)
    assert capabilities == result.capabilities
    assert len(peer.writes) == 2
    assert parse_incoming_frame(peer.writes[0]).body[1] == 0x70
    with pytest.raises(SiLabsSecurityError):
        SiLabsExchange(COMPANION_ID, "HA", keys).encrypted_request(1, 14)
    with pytest.raises(SiLabsSecurityError):
        exchange.restore(APPLIANCE_ID, peer.keys.public_key, 11)
    for appliance, version in ((b"short", 11), (APPLIANCE_ID, 4)):
        with pytest.raises(SiLabsSecurityError):
            SiLabsExchange(COMPANION_ID, "HA", keys).restore(
                appliance, peer.keys.public_key, version
            )


@pytest.mark.parametrize("status", [1, 2, 3])
async def test_rejected_timeout_and_unknown_status_never_start_secure_setup(status: int) -> None:
    peer = Peer(status)
    with pytest.raises(SiLabsSecurityError):
        await pairing.async_pair_exchange(
            peer, SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
        )
    assert len(peer.writes) == 1


async def test_pairing_timeout_and_cancellation_clear_unconfirmed_session() -> None:
    peer = Peer()
    peer.silent = True
    exchange = SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
    with pytest.raises(TimeoutError):
        await pairing.async_pair_exchange(peer, exchange, confirmation_timeout=0.01)
    assert exchange.state is ExchangeState.CANCELLED and exchange.cipher is None
    exchange = SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
    task = asyncio.create_task(pairing.async_pair_exchange(peer, exchange))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exchange.state is ExchangeState.CANCELLED


async def test_receive_budget_is_bounded_and_unrelated_messages_cannot_advance_pairing() -> None:
    peer = Peer()
    await peer.replies.put(build_outgoing_frame(1, b"\x0b\x81"))
    await peer.replies.put(build_outgoing_frame(1, b"x", encrypted=True))
    await peer.replies.put(build_outgoing_frame(1, b""))
    result = await pairing.async_pair_exchange(
        peer, SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
    )
    assert result.capabilities["probe_count"] == 4
    with patch.object(pairing, "MAX_RESPONSES", 1):
        await peer.replies.put(b"unused")
        with pytest.raises(SiLabsSecurityError, match="Too many"):
            await pairing._receive(peer, lambda _raw: False, 1)


@pytest.mark.parametrize("invalid", [b"\x0b", b"\x0c\x88", b"\x0b\x88\x01\x02\x00"])
async def test_invalid_authenticated_capabilities_are_rejected(invalid: bytes) -> None:
    keys = SiLabsSigningKeys.generate()
    peer = Peer()
    peer.cipher = SiLabsCipher(peer.keys, keys.public_key)
    exchange = SiLabsExchange(COMPANION_ID, "HA", keys)
    exchange.restore(APPLIANCE_ID, peer.keys.public_key, 11)
    writes = 0

    async def write(raw: bytes) -> None:
        nonlocal writes
        writes += 1
        body = b"\x0b\xf2" if writes == 1 else invalid
        frame = parse_incoming_frame(raw)
        await peer.replies.put(
            build_outgoing_frame(frame.sequence, peer.cipher.seal(body), encrypted=True)
        )

    with patch.object(peer, "async_write", side_effect=write):
        with pytest.raises(ValueError):
            await pairing.async_negotiate(peer, exchange, 1)


async def test_capability_negotiation_ignores_unrelated_plain_and_authenticated_frames() -> None:
    peer = Peer()
    original = peer.async_write

    async def write(raw: bytes) -> None:
        frame = parse_incoming_frame(raw)
        if frame.encrypted:
            assert peer.cipher is not None
            await peer.replies.put(build_outgoing_frame(20, b"\x0b\x88"))
            await peer.replies.put(
                build_outgoing_frame(20, peer.cipher.seal(b"\x0b\x81"), encrypted=True)
            )
        await original(raw)

    with patch.object(peer, "async_write", side_effect=write):
        result = await pairing.async_pair_exchange(
            peer, SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
        )
    assert result.capabilities["probe_count"] == 4


class GattClient:
    def __init__(self, peer: Peer, properties: set[str] | None = None) -> None:
        self.peer = peer
        self.response = SimpleNamespace(properties=properties or {"read"})
        self.command = SimpleNamespace(properties={"write"})
        self.services = SimpleNamespace(get_characteristic=self.characteristic)
        self.disconnect = AsyncMock()
        self.callback = None
        self.start_notify = AsyncMock(side_effect=self.subscribe)

    def characteristic(self, uuid: str) -> object:
        return self.command if uuid == ble.COMMAND_UUID else self.response

    async def subscribe(self, _uuid: str, callback: object) -> None:
        self.callback = callback

    async def write_gatt_char(self, _uuid: str, raw: bytes, *, response: bool) -> None:
        assert response
        await self.peer.async_write(raw)
        if self.callback is not None:
            while not self.peer.replies.empty():
                self.callback(None, bytearray(self.peer.replies.get_nowait()))

    async def read_gatt_char(self, _uuid: str) -> bytes:
        return self.peer.replies.get_nowait() if not self.peer.replies.empty() else b""


@pytest.mark.parametrize("properties", [{"read"}, {"notify"}, {"read", "notify"}])
async def test_real_bluetooth_entry_point_uses_properties_and_disconnects(
    properties: set[str],
) -> None:
    keys = SiLabsSigningKeys.generate()
    client = GattClient(Peer(), properties)
    identity = CompanionIdentity(COMPANION_ID.hex(), keys.public_key.hex())
    with patch.object(ble, "_connect", new=AsyncMock(return_value=client)):
        result = await ble.async_pair_silabs(
            object(), "address", identity, keys, progress_callback=Mock()
        )
    assert result.capabilities["probe_count"] == 4
    client.disconnect.assert_awaited_once()
    client.peer.writes.clear()
    with (
        patch.object(ble, "_connect", new=AsyncMock(return_value=client)),
        patch.object(ble, "PairingKeyStore") as storage,
    ):
        storage.return_value.async_load = AsyncMock(return_value=result.credentials)
        assert (await ble.async_reconnect_silabs(object(), "address", "ref", APPLIANCE_ID.hex()))[
            "probe_count"
        ] == 4
    assert len(client.peer.writes) == 2


async def test_channel_rejects_bad_properties_and_can_fall_back_to_reads() -> None:
    client = GattClient(Peer())
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID)
    for response in (None, SimpleNamespace(properties=set())):
        client.response = response
        with pytest.raises(ble.WeberBluetoothError):
            await channel.async_open()
    for command in (None, SimpleNamespace(properties=set())):
        client.command = command
        with pytest.raises(ble.WeberBluetoothError):
            await channel.async_write(b"ignored")
    client.response = SimpleNamespace(properties={"read", "notify"})
    client.start_notify.side_effect = BleakError("no notifications")
    await channel.async_open()
    client.response = SimpleNamespace(properties={"notify"})
    with pytest.raises(BleakError):
        await channel.async_open()


async def test_channel_drops_duplicate_frames_and_bounds_queue() -> None:
    client = GattClient(Peer())
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID)
    await channel.async_open()
    await client.peer.replies.put(b"a")
    assert await channel.async_receive() == b"a"
    await client.peer.replies.put(b"a")
    await client.peer.replies.put(b"b")
    with patch.object(ble.asyncio, "sleep", new=AsyncMock()):
        assert await channel.async_receive() == b"b"
    client.response = SimpleNamespace(properties={"notify"})
    await channel.async_open()
    assert client.callback is not None
    for _ in range(2):
        client.callback(None, bytearray(b"b"))
    client.callback(None, bytearray(b"c"))
    assert await channel.async_receive() == b"c"


@pytest.mark.parametrize("operation", ["pair", "resume"])
@pytest.mark.parametrize(
    "failure", [ValueError("invalid"), BleakError("disconnected"), TimeoutError()]
)
async def test_bluetooth_wrappers_wrap_errors_and_cleanup(
    operation: str, failure: Exception
) -> None:
    keys = SiLabsSigningKeys.generate()
    client = GattClient(Peer())
    identity = CompanionIdentity(COMPANION_ID.hex(), keys.public_key.hex())
    with (
        patch.object(ble, "_connect", new=AsyncMock(side_effect=failure)),
        patch.object(ble, "PairingKeyStore") as storage,
    ):
        storage.return_value.async_load = AsyncMock(return_value=object())
        with pytest.raises(ble.WeberBluetoothError):
            if operation == "pair":
                await ble.async_pair_silabs(object(), "address", identity, keys)
            else:
                await ble.async_reconnect_silabs(object(), "address", "ref", APPLIANCE_ID.hex())
    with patch.object(ble, "_connect", new=AsyncMock(return_value=client)):
        with pytest.raises(ble.WeberBluetoothError):
            await ble.async_pair_silabs(
                object(), "address", CompanionIdentity(COMPANION_ID.hex(), "mismatch"), keys
            )

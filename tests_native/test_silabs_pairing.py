"""Simulated real-crypto SiLabs lifecycle; no hardware or cloud is contacted."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.exc import BleakCharacteristicNotFoundError, BleakError

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
        self.version = 11

    async def async_write(self, raw: bytes) -> None:
        self.writes.append(raw)
        if self.silent:
            return
        frame = parse_incoming_frame(raw)
        if frame.encrypted:
            assert self.cipher is not None
            assert self.cipher.open(frame.body) == bytes((self.version, 0x0E))
            body = bytes((self.version, 0x88)) + self.capabilities
            await self.replies.put(
                build_outgoing_frame(frame.sequence, self.cipher.seal(body), encrypted=True)
            )
        elif frame.body[1] == 0x0A:
            assert frame.body[2:18] == COMPANION_ID
            self.cipher = SiLabsCipher(self.keys, frame.body[18:82])
            body = (
                bytes((self.version, 0x85))
                + APPLIANCE_ID
                + self.keys.public_key
                + bytes((self.status,))
            )
            await self.replies.put(build_outgoing_frame(frame.sequence, body))
        else:
            assert frame.body == bytes((self.version, 0x70)) + COMPANION_ID
            assert self.cipher is not None
            await self.replies.put(
                build_outgoing_frame(
                    frame.sequence, self.cipher.seal(bytes((self.version, 0xF2))), encrypted=True
                )
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


async def test_pairing_retries_one_lower_version_within_the_same_confirmation_budget() -> None:
    peer = Peer()
    original = peer.async_write
    first = True

    async def write(raw: bytes) -> None:
        nonlocal first
        frame = parse_incoming_frame(raw)
        if first:
            first = False
            assert frame.body[0] == 11
            peer.version = 10
            await peer.replies.put(build_outgoing_frame(frame.sequence, b"\x0a\x87\x00\x01\x00"))
        else:
            await original(raw)

    with patch.object(peer, "async_write", side_effect=write):
        result = await pairing.async_pair_exchange(
            peer, SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
        )
    assert result.message_version == result.credentials.message_version == 10
    assert parse_incoming_frame(peer.writes[0]).body[:2] == b"\x0a\x0a"


@pytest.mark.parametrize("payload", [b"\x00\x01\x01", b"\x00\x02\x00", b"\x00\x01\x00"])
async def test_invalid_or_repeated_version_errors_do_not_loop(payload: bytes) -> None:
    peer = Peer()
    calls = 0

    async def write(raw: bytes) -> None:
        nonlocal calls
        calls += 1
        sequence = parse_incoming_frame(raw).sequence
        await peer.replies.put(build_outgoing_frame(sequence, b"\x0a\x87" + payload))

    with patch.object(peer, "async_write", side_effect=write):
        with pytest.raises(SiLabsSecurityError):
            await pairing.async_pair_exchange(
                peer, SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
            )
    assert calls <= 2


def test_message_version_retry_requires_an_awaiting_supported_request() -> None:
    exchange = SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
    with pytest.raises(SiLabsSecurityError):
        exchange.retry_lower_version(2)
    exchange.message_version = 5
    exchange.pairing_request(1)
    with pytest.raises(SiLabsSecurityError):
        exchange.retry_lower_version(2)


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
        elif len(frame.body) >= 2 and frame.body[1] == 0x70:
            assert peer.cipher is not None
            await peer.replies.put(build_outgoing_frame(20, b"\x0b\xf1"))
            await peer.replies.put(
                build_outgoing_frame(19, peer.cipher.seal(b"\x0b\x81"), encrypted=True)
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


async def test_real_bluetooth_pairing_succeeds_without_progress_callback() -> None:
    keys = SiLabsSigningKeys.generate()
    client = GattClient(Peer())
    identity = CompanionIdentity(COMPANION_ID.hex(), keys.public_key.hex())
    with patch.object(ble, "_connect", new=AsyncMock(return_value=client)):
        result = await ble.async_pair_silabs(object(), "address", identity, keys)
    assert result.capabilities["probe_count"] == 4
    client.disconnect.assert_awaited_once()


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
    assert client.start_notify.await_count == 0
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


async def test_notify_only_channel_waits_for_first_notification() -> None:
    client = GattClient(Peer(), {"notify"})
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID)
    await channel.async_open()
    receive = asyncio.create_task(channel.async_receive())
    await asyncio.sleep(0)
    assert not receive.done()
    assert client.callback is not None
    client.callback(None, bytearray(b"arrived later"))
    assert await asyncio.wait_for(receive, timeout=1) == b"arrived later"


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


class LateApprovalClient(GattClient):
    """A read+notify appliance that answers only by notification, after approval.

    Reading the response characteristic while approval is pending fails, as the
    EP-series thermometer did in issue 59; the old receive path read at once and
    abandoned the request about 0.1 seconds after sending it.
    """

    def __init__(self, peer: Peer) -> None:
        super().__init__(peer, {"read", "notify"})
        self.read_gatt_char = AsyncMock(side_effect=BleakError("read not permitted"))

    async def write_gatt_char(self, _uuid: str, raw: bytes, *, response: bool) -> None:
        assert response
        await self.peer.async_write(raw)
        asyncio.get_running_loop().call_later(0.01, self.deliver)

    def deliver(self) -> None:
        assert self.callback is not None
        while not self.peer.replies.empty():
            self.callback(None, bytearray(self.peer.replies.get_nowait()))


async def test_pairing_waits_for_notified_approval_without_reading() -> None:
    keys = SiLabsSigningKeys.generate()
    client = LateApprovalClient(Peer())
    identity = CompanionIdentity(COMPANION_ID.hex(), keys.public_key.hex())
    connect = AsyncMock(return_value=client)
    with patch.object(ble, "_connect", new=connect):
        result = await ble.async_pair_silabs(object(), "address", identity, keys)
    assert result.capabilities["probe_count"] == 4
    client.read_gatt_char.assert_not_awaited()
    client.disconnect.assert_awaited_once()
    # The link-loss signal is wired to the connection, not only to the channel.
    connect.await_args.kwargs["disconnected_callback"](client)


async def test_notification_channel_falls_back_to_reads_only_without_notify() -> None:
    client = GattClient(Peer(), {"read", "notify"})
    client.start_notify.side_effect = BleakError("no notifications")
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID)
    await channel.async_open()
    await client.peer.replies.put(b"polled")
    assert await channel.async_receive() == b"polled"


async def test_link_loss_ends_the_wait_but_keeps_queued_replies() -> None:
    client = GattClient(Peer(), {"notify"})
    link_lost = asyncio.Event()
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID, link_lost)
    await channel.async_open()
    assert client.callback is not None
    receive = asyncio.create_task(channel.async_receive())
    await asyncio.sleep(0)
    link_lost.set()
    with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
        await asyncio.wait_for(receive, timeout=1)
    client.callback(None, bytearray(b"before the drop"))
    assert await channel.async_receive() == b"before the drop"
    with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
        await channel.async_receive()


@pytest.mark.parametrize("operation", ["pair", "resume"])
async def test_appliance_disconnect_while_waiting_is_reported(operation: str) -> None:
    keys = SiLabsSigningKeys.generate()
    peer = Peer()
    peer.silent = True
    client = GattClient(peer, {"notify"})
    identity = CompanionIdentity(COMPANION_ID.hex(), keys.public_key.hex())

    async def connect(*_args: object, **kwargs: object) -> GattClient:
        callback = kwargs["disconnected_callback"]
        assert callable(callback)
        asyncio.get_running_loop().call_later(0.01, callback, client)
        return client

    with (
        patch.object(ble, "_connect", new=connect),
        patch.object(ble, "PairingKeyStore") as storage,
    ):
        if operation == "pair":
            with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
                await ble.async_pair_silabs(object(), "address", identity, keys)
        else:
            peer_keys = SiLabsSigningKeys.generate()
            credentials = Mock(
                companion_id=COMPANION_ID,
                appliance_id=APPLIANCE_ID,
                keys=keys,
                peer_public_key=peer_keys.public_key,
                message_version=11,
            )
            storage.return_value.async_load = AsyncMock(return_value=credentials)
            with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
                await ble.async_reconnect_silabs(object(), "address", "ref", APPLIANCE_ID.hex())
    client.disconnect.assert_awaited_once()


async def test_abandoned_connection_attempt_does_not_mark_the_live_link_lost() -> None:
    keys = SiLabsSigningKeys.generate()
    client = LateApprovalClient(Peer())
    identity = CompanionIdentity(COMPANION_ID.hex(), keys.public_key.hex())

    async def connect(*_args: object, **kwargs: object) -> GattClient:
        callback = kwargs["disconnected_callback"]
        assert callable(callback)
        callback(object())  # The connector disconnecting a failed attempt.
        return client

    with patch.object(ble, "_connect", new=connect):
        result = await ble.async_pair_silabs(object(), "address", identity, keys)
    assert result.capabilities["probe_count"] == 4


async def test_polling_treats_failed_reads_as_no_answer_until_link_loss() -> None:
    client = GattClient(Peer(), {"read"})
    link_lost = asyncio.Event()
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID, link_lost)
    await channel.async_open()
    client.read_gatt_char = AsyncMock(side_effect=[BleakError("pending"), TimeoutError(), b"late"])
    with patch.object(ble.asyncio, "sleep", new=AsyncMock()):
        assert await channel.async_receive() == b"late"
    client.read_gatt_char = AsyncMock(side_effect=BleakError("gone"))
    link_lost.set()
    with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
        await channel.async_receive()


async def test_link_loss_during_an_outstanding_read_ends_the_wait_at_once() -> None:
    client = GattClient(Peer(), {"read"})
    link_lost = asyncio.Event()
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID, link_lost)
    await channel.async_open()
    started = asyncio.Event()

    async def hung_read(_uuid: str) -> bytes:
        started.set()
        await asyncio.Event().wait()
        return b"never"

    client.read_gatt_char = hung_read
    receive = asyncio.ensure_future(channel.async_receive())
    await started.wait()
    link_lost.set()
    with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
        await asyncio.wait_for(receive, 1)


async def test_a_read_failing_as_the_link_drops_reports_the_disconnect() -> None:
    client = GattClient(Peer(), {"read"})
    link_lost = asyncio.Event()
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID, link_lost)
    await channel.async_open()

    async def failing_read(_uuid: str) -> bytes:
        link_lost.set()
        raise BleakError("gone")

    client.read_gatt_char = failing_read
    with pytest.raises(ble.WeberBluetoothError, match="closed the Bluetooth connection"):
        await channel.async_receive()


async def test_polling_fails_fast_when_the_response_characteristic_is_gone() -> None:
    client = GattClient(Peer(), {"read"})
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID)
    await channel.async_open()
    client.read_gatt_char = AsyncMock(
        side_effect=BleakCharacteristicNotFoundError(ble.RESPONSE_UUID)
    )
    with pytest.raises(BleakCharacteristicNotFoundError):
        await channel.async_receive()
    assert client.read_gatt_char.await_count == 1


async def test_polling_still_takes_a_notification_from_a_failed_subscribe() -> None:
    client = GattClient(Peer(), {"read", "notify"})
    callbacks = []

    async def start_notify(_uuid: str, callback: object) -> None:
        callbacks.append(callback)
        raise TimeoutError

    client.start_notify = start_notify
    channel = ble.GattResponseChannel(client, ble.RESPONSE_UUID)
    await channel.async_open()
    client.read_gatt_char = AsyncMock(side_effect=BleakError("pending"))
    callbacks[0](None, bytearray(b"notified"))
    assert await channel.async_receive() == b"notified"
    assert client.read_gatt_char.await_count == 0

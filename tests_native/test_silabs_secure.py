"""Synthetic peers exercise SiLabs pairing and authenticated frames without a grill."""

from __future__ import annotations

import pytest

from custom_components.weber_connect.silabs_frames import SiLabsFrameError, build_outgoing_frame
from custom_components.weber_connect.silabs_secure import (
    ExchangeState,
    SiLabsCipher,
    SiLabsExchange,
    SiLabsSecurityError,
    SiLabsSigningKeys,
)

COMPANION_ID = bytes(range(16))
APPLIANCE_ID = bytes(range(16, 32))


def _response(peer: SiLabsSigningKeys, *, status: int = 0, sequence: int = 7) -> bytes:
    body = bytes((11, 0x85)) + APPLIANCE_ID + peer.public_key + bytes((status,))
    return build_outgoing_frame((3 << 28) | sequence, body)


def test_key_round_trip_and_ecdh_gcm_peer_interoperation() -> None:
    companion = SiLabsSigningKeys.generate()
    peer = SiLabsSigningKeys.generate()
    assert len(companion.public_key) == 64
    assert SiLabsSigningKeys.from_private_scalar(companion.private_scalar).public_key == (
        companion.public_key
    )
    outbound = SiLabsCipher(companion, peer.public_key)
    inbound = SiLabsCipher(peer, companion.public_key)
    sealed = outbound.seal(b"message")
    assert sealed != b"message" and len(sealed) == 35
    assert inbound.open(sealed) == b"message"
    with pytest.raises(SiLabsSecurityError):
        inbound.open(sealed[:-1] + bytes((sealed[-1] ^ 1,)))
    with pytest.raises(SiLabsSecurityError):
        inbound.open(b"short")
    with pytest.raises(SiLabsSecurityError):
        SiLabsCipher(companion, b"short")
    with pytest.raises(SiLabsSecurityError):
        SiLabsCipher(companion, bytes(64))
    with pytest.raises(SiLabsSecurityError):
        SiLabsSigningKeys.from_private_scalar(b"short")
    with pytest.raises(SiLabsSecurityError):
        SiLabsSigningKeys.from_private_scalar(bytes(32))


def test_confirmed_pairing_and_authenticated_reconnect() -> None:
    companion = SiLabsSigningKeys.generate()
    peer = SiLabsSigningKeys.generate()
    exchange = SiLabsExchange(COMPANION_ID, "Kitchen 🌡️" * 8, companion)
    request = exchange.pairing_request(7)
    assert request[:7] == b"\x07\x00\x00\x00" + len(request[7:]).to_bytes(2, "little") + b"\x00"
    assert request[7:9] == b"\x0b\x0a"
    assert request[9:25] == COMPANION_ID
    assert request[25:89] == companion.public_key
    assert request[89] <= 32 and len(request[90:]) == request[89]
    with pytest.raises(SiLabsSecurityError):
        exchange.pairing_request(8)
    exchange.pairing_response(_response(peer))
    assert exchange.state is ExchangeState.PAIRED
    assert exchange.appliance_id == APPLIANCE_ID
    assert exchange.appliance_public_key == peer.public_key
    greeting = exchange.handshake_greeting(8)
    assert greeting[7:9] == b"\x0b\x70" and greeting[9:] == COMPANION_ID
    peer_cipher = SiLabsCipher(peer, companion.public_key)
    authenticated = peer_cipher.seal(b"\x0b\xf2")
    success = build_outgoing_frame((3 << 28) | 9, authenticated, encrypted=True)
    assert exchange.accept_handshake(success) == 11
    assert exchange.state is ExchangeState.ACTIVE
    with pytest.raises(SiLabsSecurityError, match="replayed"):
        exchange.authenticated_body(success)
    exchange.cancel()
    assert exchange.state is ExchangeState.ACTIVE


@pytest.mark.parametrize(
    ("status", "state"), [(1, ExchangeState.REJECTED), (2, ExchangeState.TIMED_OUT)]
)
def test_pairing_rejection_statuses(status: int, state: ExchangeState) -> None:
    exchange = SiLabsExchange(COMPANION_ID, "HA", SiLabsSigningKeys.generate())
    exchange.pairing_request(7)
    exchange.pairing_response(_response(SiLabsSigningKeys.generate(), status=status))
    assert exchange.state is state
    with pytest.raises(SiLabsSecurityError):
        exchange.handshake_greeting(8)


def test_pairing_response_rejects_unmatched_or_malformed_frames() -> None:
    keys = SiLabsSigningKeys.generate()
    peer = SiLabsSigningKeys.generate()
    with pytest.raises(SiLabsSecurityError):
        SiLabsExchange(b"short", "HA", keys)
    exchange = SiLabsExchange(COMPANION_ID, "HA", keys)
    with pytest.raises(SiLabsSecurityError):
        exchange.pairing_response(_response(peer))
    exchange.pairing_request(7)
    valid = _response(peer)
    bad_body = bytearray(valid)
    bad_body[8] = 0x81
    bad_status = bytearray(valid)
    bad_status[-1] = 3
    bad_point = bytearray(valid)
    bad_point[25:89] = bytes(64)
    for invalid in (
        _response(peer, sequence=8),
        build_outgoing_frame(7, valid[7:], encrypted=True),
        valid[:-1],
        bytes(bad_body),
        bytes(bad_status),
        bytes(bad_point),
    ):
        with pytest.raises((SiLabsSecurityError, SiLabsFrameError)):
            exchange.pairing_response(invalid)
    assert exchange.state is ExchangeState.AWAITING_CONFIRMATION
    exchange.cancel()
    assert exchange.state is ExchangeState.CANCELLED and exchange.cipher is None
    exchange.cancel()


def test_handshake_rejects_unencrypted_tampered_and_wrong_type() -> None:
    keys = SiLabsSigningKeys.generate()
    peer = SiLabsSigningKeys.generate()
    exchange = SiLabsExchange(COMPANION_ID, "HA", keys)
    with pytest.raises(SiLabsSecurityError):
        exchange.handshake_greeting(1)
    exchange.pairing_request(7)
    exchange.pairing_response(_response(peer))
    with pytest.raises(SiLabsSecurityError):
        exchange.accept_handshake(b"")
    exchange.handshake_greeting(8)
    peer_cipher = SiLabsCipher(peer, keys.public_key)
    with pytest.raises(SiLabsSecurityError):
        exchange.authenticated_body(build_outgoing_frame(9, b"\x0b\xf2"))
    with pytest.raises(SiLabsFrameError):
        exchange.authenticated_body(build_outgoing_frame(9, b"short", encrypted=True))
    bad_type = build_outgoing_frame(9, peer_cipher.seal(b"\x0b\x81"), encrypted=True)
    with pytest.raises(SiLabsSecurityError):
        exchange.accept_handshake(bad_type)
    too_new = build_outgoing_frame(10, peer_cipher.seal(b"\x0c\xf2"), encrypted=True)
    with pytest.raises(SiLabsSecurityError):
        exchange.accept_handshake(too_new)
    exchange.cancel()
    assert exchange.state is ExchangeState.CANCELLED

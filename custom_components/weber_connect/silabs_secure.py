"""SiLabs appliance key exchange and authenticated message boundary.

The Android 2.12.0.2665 app uses raw P-256 X/Y keys, ECDH, SHA-256 and
AES-GCM. This module is independent of GATT so simulated peers can verify the
contract before an appliance is allowed to exchange pairing commands.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import StrEnum

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .silabs_frames import SiLabsFrame, SiLabsFrameError, build_outgoing_frame, parse_incoming_frame

MESSAGE_VERSION = 11
PAIRING_REQUEST = 0x0A
PAIRING_RESPONSE = 0x85
HANDSHAKE_GREETING = 0x70
HANDSHAKE_SUCCESS = 0xF2
MAX_DISPLAY_NAME_BYTES = 32
ENCRYPTED_OVERHEAD = 28  # Twelve-byte nonce and sixteen-byte GCM tag.


class SiLabsSecurityError(ValueError):
    """A key, pairing response, or authenticated message is invalid."""


class ExchangeState(StrEnum):
    """Pairing and reconnect milestones, independent of UI or cloud setup."""

    NEW = "new"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PAIRED = "paired"
    HANDSHAKING = "handshaking"
    ACTIVE = "active"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    point = key.public_numbers()
    return point.x.to_bytes(32, "big") + point.y.to_bytes(32, "big")


def _load_public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
    if len(raw) != 64:
        raise SiLabsSecurityError("SiLabs public key must contain 64 bytes.")
    try:
        return ec.EllipticCurvePublicNumbers(
            int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"), ec.SECP256R1()
        ).public_key()
    except ValueError as exc:
        raise SiLabsSecurityError("SiLabs public key is not on the P-256 curve.") from exc


@dataclass(frozen=True, slots=True)
class SiLabsSigningKeys:
    """Identity material kept private; callers must store it as a secret."""

    private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def generate(cls) -> SiLabsSigningKeys:
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @property
    def public_key(self) -> bytes:
        return _public_bytes(self.private_key.public_key())

    @property
    def private_scalar(self) -> bytes:
        """Raw 32-byte scalar used by the official companion's pairing store."""

        return self.private_key.private_numbers().private_value.to_bytes(32, "big")

    @classmethod
    def from_private_scalar(cls, raw: bytes) -> SiLabsSigningKeys:
        if len(raw) != 32:
            raise SiLabsSecurityError("SiLabs private scalar must contain 32 bytes.")
        try:
            return cls(ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1()))
        except ValueError as exc:
            raise SiLabsSecurityError("SiLabs private scalar is invalid.") from exc


class SiLabsCipher:
    """AES-256-GCM session derived from the two exchanged pairing keys."""

    def __init__(self, own_keys: SiLabsSigningKeys, peer_public_key: bytes) -> None:
        shared = own_keys.private_key.exchange(ec.ECDH(), _load_public_key(peer_public_key))
        self._cipher = AESGCM(hashlib.sha256(shared).digest())

    def seal(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._cipher.encrypt(nonce, plaintext, None)

    def open(self, sealed: bytes) -> bytes:
        if len(sealed) < ENCRYPTED_OVERHEAD:
            raise SiLabsSecurityError("Encrypted SiLabs body is shorter than nonce and tag.")
        try:
            return self._cipher.decrypt(sealed[:12], sealed[12:], None)
        except InvalidTag as exc:
            raise SiLabsSecurityError("SiLabs authenticated message failed verification.") from exc


class SiLabsExchange:
    """A single pairing attempt and its later authenticated greeting.

    The caller owns all transport deadlines and cancellation. No device write
    occurs inside this class, and no credentials or packet bytes are logged.
    The plaintext confirmation supplies a key, not a manufacturer identity.
    GCM proves possession of that key; the setup caller must separately require
    physical confirmation and the fresh companion's exact cloud association.
    """

    def __init__(self, companion_id: bytes, display_name: str, keys: SiLabsSigningKeys) -> None:
        if len(companion_id) != 16:
            raise SiLabsSecurityError("SiLabs companion ID must contain 16 bytes.")
        self.companion_id = companion_id
        self.display_name = display_name
        self.keys = keys
        self.state = ExchangeState.NEW
        self.appliance_id: bytes | None = None
        self.appliance_public_key: bytes | None = None
        self.cipher: SiLabsCipher | None = None
        self.message_version = MESSAGE_VERSION
        self._request_sequence: int | None = None
        self._seen_incoming: set[tuple[int | None, int]] = set()
        self._seen_nonces: set[bytes] = set()

    def pairing_request(self, sequence: int) -> bytes:
        if self.state is not ExchangeState.NEW:
            raise SiLabsSecurityError("Pairing request was already started.")
        name = self.display_name.encode("utf-8")[:MAX_DISPLAY_NAME_BYTES]
        # Truncate to a complete UTF-8 code point, as an appliance name is text.
        name = name.decode("utf-8", errors="ignore").encode("utf-8")
        body = (
            bytes((self.message_version, PAIRING_REQUEST))
            + self.companion_id
            + self.keys.public_key
        )
        body += bytes((len(name),)) + name
        result = build_outgoing_frame(sequence, body)
        self._request_sequence = sequence
        self.state = ExchangeState.AWAITING_CONFIRMATION
        return result

    def retry_lower_version(self, sequence: int) -> bytes:
        if self.state is not ExchangeState.AWAITING_CONFIRMATION or self.message_version <= 5:
            raise SiLabsSecurityError("SiLabs message version cannot be lowered for this request.")
        self.message_version -= 1
        self.state = ExchangeState.NEW
        return self.pairing_request(sequence)

    def pairing_response(self, raw_frame: bytes) -> None:
        if self.state is not ExchangeState.AWAITING_CONFIRMATION:
            raise SiLabsSecurityError("No SiLabs pairing request is awaiting a response.")
        frame = parse_incoming_frame(raw_frame)
        if frame.encrypted or frame.sequence != (self._request_sequence or 0) & 0x0FFFFFFF:
            raise SiLabsSecurityError("SiLabs pairing response has the wrong trust context.")
        body = frame.plaintext_body()
        if len(body) != 83 or body[1] != PAIRING_RESPONSE or not 5 <= body[0] <= MESSAGE_VERSION:
            raise SiLabsSecurityError("SiLabs pairing response has an invalid envelope.")
        status = body[82]
        if status == 1:
            self.state = ExchangeState.REJECTED
            return
        if status == 2:
            self.state = ExchangeState.TIMED_OUT
            return
        if status != 0:
            raise SiLabsSecurityError("SiLabs pairing response has an unknown status.")
        peer_key = body[18:82]
        cipher = SiLabsCipher(self.keys, peer_key)
        self.appliance_id = body[2:18]
        self.appliance_public_key = peer_key
        self.cipher = cipher
        self.message_version = body[0]
        self.state = ExchangeState.PAIRED

    def handshake_greeting(self, sequence: int) -> bytes:
        if self.state is not ExchangeState.PAIRED:
            raise SiLabsSecurityError("A confirmed pairing is required for the handshake.")
        self.state = ExchangeState.HANDSHAKING
        return build_outgoing_frame(
            sequence, bytes((self.message_version, HANDSHAKE_GREETING)) + self.companion_id
        )

    def accept_handshake(self, raw_frame: bytes, *, ignore_unrelated: bool = False) -> int | None:
        if self.state is not ExchangeState.HANDSHAKING:
            raise SiLabsSecurityError("No SiLabs secure handshake is in progress.")
        body = self.authenticated_body(raw_frame)
        if len(body) >= 2 and body[1] != HANDSHAKE_SUCCESS and ignore_unrelated:
            return None
        if len(body) < 2 or body[1] != HANDSHAKE_SUCCESS or not 5 <= body[0] <= MESSAGE_VERSION:
            raise SiLabsSecurityError("SiLabs handshake success has an invalid message type.")
        self.state = ExchangeState.ACTIVE
        self.message_version = body[0]
        return body[0]

    def restore(self, appliance_id: bytes, peer_public_key: bytes, version: int) -> None:
        """Resume a stored identity without issuing a new pairing request."""

        if self.state is not ExchangeState.NEW or len(appliance_id) != 16 or not 5 <= version <= 11:
            raise SiLabsSecurityError("Cannot restore the SiLabs pairing record.")
        self.cipher = SiLabsCipher(self.keys, peer_public_key)
        self.appliance_id = appliance_id
        self.appliance_public_key = peer_public_key
        self.message_version = version
        self.state = ExchangeState.PAIRED

    def encrypted_request(self, sequence: int, message_type: int, payload: bytes = b"") -> bytes:
        if self.state is not ExchangeState.ACTIVE or self.cipher is None:
            raise SiLabsSecurityError("A successful SiLabs handshake is required.")
        sealed = self.cipher.seal(bytes((self.message_version, message_type)) + payload)
        return build_outgoing_frame(sequence, sealed, encrypted=True)

    def authenticated_body(self, raw_frame: bytes) -> bytes:
        frame: SiLabsFrame = parse_incoming_frame(raw_frame)
        if not frame.encrypted or self.cipher is None:
            raise SiLabsSecurityError("An authenticated SiLabs session is required.")
        if len(frame.body) < ENCRYPTED_OVERHEAD:
            raise SiLabsFrameError("SiLabs encrypted frame is truncated.")
        body = self.cipher.open(frame.body)
        marker = (frame.boot_count, frame.sequence)
        nonce = frame.body[:12]
        if marker in self._seen_incoming or nonce in self._seen_nonces:
            raise SiLabsSecurityError("SiLabs incoming sequence was replayed.")
        self._seen_incoming.add(marker)
        self._seen_nonces.add(nonce)
        return body

    def cancel(self) -> None:
        if self.state not in (
            ExchangeState.ACTIVE,
            ExchangeState.REJECTED,
            ExchangeState.TIMED_OUT,
        ):
            self.state = ExchangeState.CANCELLED
        self.cipher = None

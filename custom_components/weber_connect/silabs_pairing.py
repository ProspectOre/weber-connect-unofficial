"""Bounded SiLabs pairing, authenticated reconnect and capability negotiation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast

from .capabilities import parse_capabilities
from .models import PairingResult
from .pairing_storage import SiLabsCredentials
from .silabs_frames import parse_incoming_frame
from .silabs_secure import ExchangeState, SiLabsExchange, SiLabsSecurityError
from .support import SupportEvent

MAX_RESPONSES = 128
HANDSHAKE_TIMEOUT = 10.0
CAPABILITIES_TIMEOUT = 10.0
FETCH_CAPABILITIES = 0x0E
INCOMING_CAPABILITIES = 0x88


class SiLabsChannel(Protocol):
    async def async_write(self, data: bytes) -> None: ...

    async def async_receive(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class SiLabsPairingResult(PairingResult):
    credentials: SiLabsCredentials = field(repr=False)
    capabilities: dict[str, int]


async def _receive(
    channel: SiLabsChannel, accept: Callable[[bytes], bool], timeout: float
) -> bytes:
    async with asyncio.timeout(timeout):
        for _ in range(MAX_RESPONSES):
            frame = await channel.async_receive()
            if accept(frame):
                return frame
    raise SiLabsSecurityError("Too many unrelated SiLabs responses.")


async def async_negotiate(
    channel: SiLabsChannel, exchange: SiLabsExchange, sequence: int
) -> dict[str, int]:
    """Prove possession of paired keys before decoding capability responses."""

    async with asyncio.timeout(HANDSHAKE_TIMEOUT):
        await channel.async_write(exchange.handshake_greeting(sequence))
        # Handshake success is accepted only after GCM authentication; unrelated
        # plaintext cannot advance the session or become application telemetry.
        raw = await _receive(
            channel, lambda frame: parse_incoming_frame(frame).encrypted, HANDSHAKE_TIMEOUT
        )
        exchange.accept_handshake(raw)
    request_sequence = sequence + 1
    async with asyncio.timeout(CAPABILITIES_TIMEOUT):
        await channel.async_write(exchange.encrypted_request(request_sequence, FETCH_CAPABILITIES))
        capabilities: dict[str, int] = {}

        def accept(raw: bytes) -> bool:
            nonlocal capabilities
            frame = parse_incoming_frame(raw)
            if not frame.encrypted:
                return False
            body = exchange.authenticated_body(raw)
            if len(body) < 2:
                raise SiLabsSecurityError("Authenticated SiLabs message is incomplete.")
            if frame.sequence != request_sequence or body[1] != INCOMING_CAPABILITIES:
                return False
            if not 5 <= body[0] <= exchange.message_version:
                raise SiLabsSecurityError("SiLabs capabilities use an unsupported message version.")
            capabilities = parse_capabilities(body[2:])
            return True

        await _receive(channel, accept, CAPABILITIES_TIMEOUT)
    return capabilities


async def async_pair_exchange(
    channel: SiLabsChannel,
    exchange: SiLabsExchange,
    *,
    confirmation_timeout: float = 60.0,
    progress_callback: Callable[[SupportEvent], None] | None = None,
) -> SiLabsPairingResult:
    """Execute the app's request/confirmation/secure-greeting/capability flow."""

    try:
        async with asyncio.timeout(confirmation_timeout):
            await channel.async_write(exchange.pairing_request(1))
            if progress_callback is not None:
                progress_callback(SupportEvent.REQUESTED)

            def accept(raw: bytes) -> bool:
                frame = parse_incoming_frame(raw)
                if frame.encrypted or len(frame.body) < 2 or frame.body[1] != 0x85:
                    return False
                exchange.pairing_response(raw)
                return True

            await _receive(channel, accept, confirmation_timeout)
        if exchange.state is not ExchangeState.PAIRED:
            raise SiLabsSecurityError(f"SiLabs pairing was {exchange.state.value}.")
        if progress_callback is not None:
            progress_callback(SupportEvent.CONFIRMED)
        capabilities = await async_negotiate(channel, exchange, 2)
        appliance_id = cast(bytes, exchange.appliance_id)
        peer_key = cast(bytes, exchange.appliance_public_key)
        credentials = SiLabsCredentials(
            exchange.companion_id,
            appliance_id,
            exchange.keys,
            peer_key,
            exchange.message_version,
        )
        return SiLabsPairingResult(
            exchange.message_version, appliance_id.hex(), credentials, capabilities
        )
    finally:
        exchange.cancel()


async def async_resume_exchange(
    channel: SiLabsChannel, credentials: SiLabsCredentials
) -> dict[str, int]:
    credentials.validate()
    exchange = SiLabsExchange(credentials.companion_id, "Home Assistant", credentials.keys)
    exchange.restore(
        credentials.appliance_id, credentials.peer_public_key, credentials.message_version
    )
    try:
        return await async_negotiate(channel, exchange, 1)
    finally:
        exchange.cancel()

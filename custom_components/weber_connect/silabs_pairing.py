"""Bounded SiLabs pairing, authenticated reconnect and capability negotiation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, cast

from .capabilities import parse_capabilities
from .models import PairingResult
from .pairing_storage import SiLabsCredentials
from .saber_frames import parse_tlv
from .silabs_frames import parse_incoming_frame
from .silabs_secure import HANDSHAKE_SUCCESS, ExchangeState, SiLabsExchange, SiLabsSecurityError
from .support import SupportEvent

_LOGGER = logging.getLogger(__name__)

MAX_RESPONSES = 128
HANDSHAKE_TIMEOUT = 10.0
CAPABILITIES_TIMEOUT = 10.0
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
    channel: SiLabsChannel,
    exchange: SiLabsExchange,
    sequence: int,
    *,
    fresh_pairing: bool = False,
    progress_callback: Callable[[SupportEvent], None] | None = None,
) -> dict[str, int]:
    """Prove possession of paired keys before decoding capability responses."""

    _LOGGER.debug("Waiting for SiLabs authenticated handshake")
    async with asyncio.timeout(HANDSHAKE_TIMEOUT):
        await channel.async_write(exchange.handshake_greeting(sequence))
        if progress_callback is not None:
            progress_callback(SupportEvent.HANDSHAKE)

        # A short EP acknowledgement can prompt the encrypted capabilities read.
        # It cannot authenticate the peer or supply a negotiated version.
        def handshake(raw: bytes) -> bool:
            if raw == bytes((HANDSHAKE_SUCCESS,)):
                return fresh_pairing
            frame = parse_incoming_frame(raw)
            if not frame.encrypted:
                return fresh_pairing and frame.body == bytes(
                    (exchange.message_version, HANDSHAKE_SUCCESS)
                )
            return exchange.accept_handshake(raw, ignore_unrelated=True) is not None

        await _receive(channel, handshake, HANDSHAKE_TIMEOUT)
    _LOGGER.debug("SiLabs greeting answered, requesting authenticated capabilities")
    async with asyncio.timeout(CAPABILITIES_TIMEOUT):
        await channel.async_write(exchange.capabilities_request(sequence + 1))
        if progress_callback is not None:
            progress_callback(SupportEvent.CAPABILITIES)
        capabilities: dict[str, int] = {}

        def accept(raw: bytes) -> bool:
            nonlocal capabilities
            if raw == bytes((HANDSHAKE_SUCCESS,)):
                return False
            frame = parse_incoming_frame(raw)
            if not frame.encrypted:
                return False
            body = exchange.authenticated_body(raw)
            if len(body) < 2:
                raise SiLabsSecurityError("Authenticated SiLabs message is incomplete.")
            if body[1] != INCOMING_CAPABILITIES:
                return False
            if not 5 <= body[0] <= exchange.message_version:
                raise SiLabsSecurityError("SiLabs capabilities use an unsupported message version.")
            capabilities = parse_capabilities(body[2:])
            exchange.message_version = body[0]
            exchange.state = ExchangeState.ACTIVE
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
            request = exchange.pairing_request(1)
            sequence = 1
            attempt = 0
            while True:
                await channel.async_write(request)
                if progress_callback is not None:
                    progress_callback(SupportEvent.REQUESTED)

                # The controller owns the incoming counter. Match the message
                # type to the one outstanding request on this connection.
                def accept(raw: bytes) -> bool:
                    frame = parse_incoming_frame(raw)
                    return (
                        not frame.encrypted
                        and len(frame.body) >= 2
                        and frame.body[1] in (0x85, 0x87)
                    )

                _LOGGER.debug("Waiting for SiLabs physical pairing response")
                raw = await _receive(channel, accept, confirmation_timeout)
                body = parse_incoming_frame(raw).body
                if body[1] == 0x87:
                    fields = parse_tlv(body[2:])
                    if attempt or -1 in fields or fields.get(0) != [b"\x00"]:
                        raise SiLabsSecurityError("SiLabs pairing version negotiation failed.")
                    sequence += 1
                    attempt += 1
                    request = exchange.retry_lower_version(sequence)
                    continue
                exchange.pairing_response(raw)
                _LOGGER.debug("SiLabs pairing response decoded: %s", exchange.state.value)
                break
        if exchange.state is not ExchangeState.PAIRED:
            raise SiLabsSecurityError(f"SiLabs pairing was {exchange.state.value}.")
        if progress_callback is not None:
            progress_callback(SupportEvent.CONFIRMED)
        capabilities = await async_negotiate(
            channel, exchange, sequence + 1, fresh_pairing=True, progress_callback=progress_callback
        )
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

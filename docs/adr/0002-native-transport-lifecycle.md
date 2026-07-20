# ADR 0002: Native transport lifecycle

## Status

Accepted for 3.0 implementation. Production validation is required before
release.

## Context

The first native prototype reused the add-on's connect/read/disconnect loop.
That made direct Bluetooth easy to prove, but it was not a sound native design:
an ESPHome proxy had to allocate and release a remote GATT slot for every
sample, cloud reads repeated cook-history requests that were outside the 3.0
entity scope, and an optional fallback could compete with the Weber app for the
hub's single Bluetooth connection.

3.0 exposes four permanent probe-temperature entities and nothing else. The
runtime architecture should be derived from that product contract rather than
from the removed add-on.

## Decision

Each config entry owns exactly one long-lived transport selected by the user:

- **Phone + Home Assistant** owns one authenticated Weber companion WebSocket.
  The official app remains free to own the hub's Bluetooth connection.
- **Home Assistant only** owns one GATT connection through Home Assistant's
  selected local adapter or active ESPHome proxy. It subscribes once, retains
  the proxy slot, and reconnects only after an actual link loss.

Both transports publish decoded status messages into one push coordinator.
There is no user-configurable polling interval and no automatic cross-transport
fallback. A transport is closed before another can start, and config-entry
unload cancels every entry-owned task and releases its WebSocket or GATT
connection.

The normalized runtime state contains only support metadata and the four probe
slots. Raw cook sessions, recipe text, instructions, cavities, timers, control
commands, and transient pairing keys are not persisted or returned by
diagnostics.

Expected idle behavior is represented by four visible temperature entities
with `Unknown` values and probe-off icons. Local hub sleep is not a repair
condition. Sustained cloud failure may create one actionable repair because
internet access is required for the selected mode.

## Invariants

1. A config entry never owns cloud and Bluetooth sessions simultaneously.
2. A successful local sample does not disconnect the GATT client.
3. A proxy slot is released on link failure, entry reload, and Home Assistant
   shutdown.
4. Bluetooth path selection always goes through Home Assistant; the integration
   never connects to an ESPHome proxy directly or handles its credentials.
5. Cloud status uses the companion WebSocket only after setup; cook-history REST
   data is not part of the 3.0 runtime path.
6. Exactly four entities exist, and their unique IDs depend only on the hub and
   physical probe number.
7. Diagnostics contain no raw protocol frames, credentials, device identifiers,
   recipe metadata, or instruction text.
8. An empty or sleeping hub remains a normal visible idle state, not a device
   disappearance.

## Consequences

The architecture is smaller and easier to explain, proxy traffic is bounded to
one remote connection, and cloud cadence is no longer lengthened by unrelated
history requests. Users who change connection mode reload the config entry so
the old transport is closed before the new one starts. Automatic Bluetooth
fallback is deliberately omitted because transparent failover would violate
the phone-access guarantee of the recommended mode.


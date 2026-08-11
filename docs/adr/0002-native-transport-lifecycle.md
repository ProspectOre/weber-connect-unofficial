# ADR 0002: Native transport lifecycle

## Status

Accepted, implemented, and production-validated for 3.0 on the documented
Home Assistant Yellow, Weber hub, and single ESPHome proxy setup. Direct
host-adapter operation and two-proxy failover remain explicitly unverified.

## Context

The first native prototype reused the add-on's connect/read/disconnect loop.
That made direct Bluetooth easy to prove, but it was not a sound native design:
an ESPHome proxy had to allocate and release a remote GATT slot for every
sample, cloud reads repeated cook-history requests that were outside the 3.0
entity scope, and an optional fallback could compete with the Weber app for the
hub's single Bluetooth connection.

3.0 introduced stable probe-temperature entities plus two concise
connection-context entities. Version 3.0.4 makes the entity surface
model-aware: Probe 1 and Probe 2 are the baseline, while Probe 3 and Probe 4 are
created only after their slot numbers appear in decoded status. The runtime
architecture should be derived from that product contract rather than from the
removed add-on.

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

The normalized runtime state can represent up to four probe slots and records
which slot numbers were present in the latest decoded status. It also contains
the current connection state and method, the last successful update time, and
capability-driven appliance, cook, session, timer, and burner telemetry. Recipe
text, instructions, control commands, transient pairing keys, and raw protocol
frames are not persisted or returned by diagnostics.

Expected idle behavior is represented by every registered probe-temperature
entity retaining an `Unknown` value and probe-off icon. Hub sleep, power-off,
loss of Wi-Fi, and temporary cloud outages are routine availability states, so
they recover quietly without a repair issue. Only a rejected generated
companion credential creates a repair because it cannot recover without pairing
again.

## Invariants

1. A config entry never owns cloud and Bluetooth sessions simultaneously.
2. A successful local sample does not disconnect the GATT client.
3. A proxy slot is released on link failure, entry reload, and Home Assistant
   shutdown.
4. Bluetooth path selection always goes through Home Assistant; the integration
   never connects to an ESPHome proxy directly or handles its credentials.
5. Cloud status uses the companion WebSocket only after setup; cook-history REST
   data is not part of the 3.0 runtime path.
6. Probe 1 and Probe 2 are baseline entities. Probe 3 and Probe 4 are created
   only after their slots are reported. Their unique IDs depend only on the hub
   and semantic entity key.
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

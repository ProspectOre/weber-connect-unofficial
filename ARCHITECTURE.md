# Architecture

The add-on is a supervised BLE-first telemetry pipeline. Recommended onboarding
creates a bridge-owned cloud companion, with local-only pairing as a fallback:

```text
Home Assistant ingress
        │
        ▼
HTTP adapter ──► Hub controller/state machine ──► BLE adapter ──► Weber hub
                         │                              preferred
                         ├──► cloud coexistence adapter ──► Weber private API
                         ├──► private atomic JSON state
                         └──► persistent MQTT session ──► Home Assistant
```

## Runtime Boundaries

- `weber_panel.py` owns product behavior, pairing, handoff, transport selection,
  and state transitions. Infrastructure is injected through
  `ControllerDependencies` for deterministic tests.
- `weber_runtime.py` defines validated settings, connection states, retry
  policy, mutable runtime state, and background-task supervision.
- `weber_http.py` owns Home Assistant ingress, request limits, timeouts, static
  assets, security headers, and API dispatch.
- `weber_mqtt.py` owns one reusable MQTT connection, bounded acknowledgements,
  discovery delivery, availability, and retained state.
- `weber_cloud.py` owns companion registration and authentication, appliance
  association, cook-session discovery, paged snapshots, and temperature
  normalization for Weber's private API.
- `weber_persistence.py` owns collision-safe atomic JSON replacement, private
  file modes, and file/directory fsync.
- `weber_status_bridge.py`, `weber_ble_pair.py`, `weber_ble_scan.py`, and
  `saber_frames.py` implement the BLE protocol and diagnostic CLI surfaces.

## Universal Cloud Pairing Lifecycle

The bridge never needs the user's Weber account or the phone app's companion
password.

1. Generate a random 16-byte companion ID, random device password, and random
   companion key material.
2. Register that companion with Weber Cloud before presenting its ID to the
   hub.
3. Pair the same companion with the hub over BLE and require physical
   confirmation when the hub prompts.
4. Start the paired companion session so the hub and bridge derive the session
   state from the exchanged nonce and companion/appliance key material.
5. Poll appliance association for up to five minutes because Weber's backend
   can publish it asynchronously.
6. Commit the private identity and appliance association only to the add-on's
   data directory. Normal status responses reveal only a short ID suffix.

Once associated, the hub may upload cook snapshots to Weber Cloud independently
of whichever client owns BLE. During phone handoff the official app owns BLE and
the add-on polls those snapshots. The cloud transport is not a second BLE
connection.

Weber snapshot temperatures are encoded in tenths of a degree Celsius. The
normalization boundary publishes both Celsius and Fahrenheit values.

## Invariants

1. At most one bridge BLE operation owns the hub at a time.
2. Scan and pairing become single-flight before the API acknowledges them.
3. Every background task belongs to `TaskSupervisor` and is cancelled and
   awaited during shutdown.
4. Shutdown closes MQTT, drains HTTP, and explicitly releases BlueZ.
5. Successful reads are scheduled start-to-start; failures use bounded
   exponential backoff with jitter.
6. Failed reads publish explicit disconnected MQTT state. Retained panel values
   are visibly marked stale.
7. Phone handoff survives restarts and ends only at its deadline, explicit
   resume, or hub removal.
8. Pairing keys, cloud credentials, settings, handoff state, and status files
   are private (`0600`) and atomically replaced.
9. MQTT connection and acknowledgement waits are bounded. Unexpected disconnect
   uses the configured last will.
10. BLE is preferred whenever available. Cloud polling occurs only after opt-in
    and when BLE is handed off or unavailable.
11. The cloud path is telemetry-only: no Wi-Fi provisioning, recipe start,
    target modification, timer modification, or grill control.
12. Authentication success alone is insufficient; setup verifies that the
    identity can access the paired appliance.

## Verification

CI enforces Ruff, strict mypy coverage of the runtime, branch coverage,
malformed-frame robustness, dependency auditing, release validation, and
multi-architecture container smoke tests. Runtime dependencies are hash-pinned.

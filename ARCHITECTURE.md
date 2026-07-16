# Architecture

The add-on is a supervised local pipeline:

```text
Home Assistant ingress
        │
        ▼
HTTP adapter ──► Hub controller/state machine ──► BLE adapter ──► Weber hub
                         │
                         ├──► durable private JSON state
                         │
                         └──► persistent MQTT session ──► Home Assistant
```

## Runtime Boundaries

- `weber_panel.py` owns product behavior and state transitions. Infrastructure
  is injected through `ControllerDependencies`, which keeps BLE and MQTT
  failure paths deterministic in tests.
- `weber_runtime.py` defines validated settings, connection states, retry
  policy, mutable runtime state, and background-task supervision.
- `weber_http.py` owns the ingress HTTP boundary, request limits, timeouts,
  static assets, response headers, and API dispatch.
- `weber_mqtt.py` owns a single reusable MQTT connection, bounded publish
  acknowledgements, discovery delivery, and retained online/offline status.
- `weber_persistence.py` owns collision-safe atomic JSON replacement, private
  file modes, and file/directory fsync.
- `weber_status_bridge.py`, `weber_ble_pair.py`, `weber_ble_scan.py`, and
  `saber_frames.py` contain the BLE protocol and diagnostic CLI surfaces.

## Invariants

1. At most one BLE operation owns the hub at a time.
2. Scan and pairing become single-flight before the API acknowledges them.
3. Every background task belongs to `TaskSupervisor` and is cancelled and
   awaited during shutdown.
4. Shutdown closes MQTT, drains HTTP, and explicitly releases the BlueZ
   connection before the process exits.
5. Successful reads are scheduled start-to-start. Failures use bounded
   exponential backoff with small jitter.
6. A failed read publishes an explicit disconnected MQTT state. The panel may
   still show the last good reading, but labels it as stale.
7. Phone handoff state is durable across restarts and is cleared only by its
   deadline, explicit resume, or forgetting the hub.
8. Pairing keys, settings, handoff state, and status files are private (`0600`)
   and atomically replaced.
9. MQTT publishing has bounded connection and acknowledgement waits. An
   unexpected disconnect publishes the configured last will.

## Verification

CI enforces formatting/lint rules, strict typing on the runtime boundary,
branch coverage, malformed-frame robustness, dependency auditing, release
validation, and multi-architecture container smoke tests. Runtime dependency
versions and hashes are generated from `requirements.in` into
`requirements.txt`.

# Changelog

## 1.1.0 — 2026-07-16

- Reduced the privilege surface: removed `NET_ADMIN`, `NET_RAW`, and `udev`;
  Bluetooth uses the BlueZ D-Bus interface exclusively. Added a scoped
  AppArmor profile.
- Added a Supervisor watchdog on a new lock-free `/api/health` endpoint, and
  required ingress provenance on all mutating panel routes.
- Stopped MQTT discovery churn: discovery is published once per probe and
  persisted, so offline/online cycles no longer delete and recreate entities.
- Added hub availability that reflects the connection state, per-probe
  availability topics, and bridge health entities (connectivity and last
  publish time).
- Made status-file write failures recoverable instead of fatal, and reduced
  pairing log noise to debug level.
- Brought the entire runtime, including the BLE protocol core, under strict
  type checking; raised the enforced test-coverage floor from 55% to 95%
  (255 tests, branch coverage).
- Hardened the release pipeline: refused overwriting published image
  versions, pinned all CI actions to commit digests, pinned the base image
  by digest, added SBOM/provenance attestations and image signing, and
  extended automated dependency updates to pip and Docker.
- Replaced the plaintext privacy denylist with hashed identifiers and added
  pattern-based scanning for hardware addresses across the tree.

## 1.0.0 — 2026-07-12

- Rebuilt the panel runtime around explicit typed state, injected BLE/MQTT
  boundaries, supervised background tasks, and deterministic graceful shutdown.
- Added persistent MQTT connections with availability/LWT state and bounded
  connect/publish waits.
- Added durable phone handoff across restarts, atomic private persistence,
  completion-driven BLE reads, disconnected-state publishing, and bounded
  exponential retry backoff.
- Added hardened ingress HTTP limits and headers plus integration, lifecycle,
  persistence, MQTT, malformed-frame, lint, typing, coverage, and dependency
  audit gates.

## 0.1.0

First public release.

- Pairs with the Weber Connect Smart Grilling Hub directly over Bluetooth —
  fully local, no Weber cloud or account required. When the hub beeps during
  pairing, press the button on the hub to confirm.
- Built-in web panel (ingress) with one-tap **Find My Hub** pairing, live hub
  and probe status, **Use with Phone** handoff with automatic reconnect, and
  **Forget This Hub**.
- Publishes probe temperature and state sensors through MQTT discovery; a
  battery sensor is added automatically for wireless probes. Empty probe
  slots read "No probe".
- Implements the hub's BLE protocol natively: session-slot claim, protocol
  version negotiation, frame encoding/decoding, and pairing key exchange
  with a P-256 keypair stored with owner-only permissions.
- Installs from prebuilt images (aarch64, amd64) published to GitHub
  Container Registry — installs and updates complete in seconds.

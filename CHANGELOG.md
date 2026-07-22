# Changelog

## 3.0.1 — 2026-07-21

- Treat a sleeping, powered-off, or temporarily unreachable hub as normal idle
  behavior instead of raising a Home Assistant repair issue.
- Continue quiet background recovery while retaining all four probe entities
  as `Unknown` until fresh readings return.
- Preserve the actionable repair only for a genuinely rejected generated
  companion credential, which requires pairing again.
- Clear connection-loss repair records created by 3.0.0 when the integration
  starts after an update.
- Update the pinned WebSocket runtime from 16.1 to 16.1.1.

## 3.0.0 — 2026-07-21

- Introduced a native Home Assistant custom integration with automatic device
  discovery and native entities.
- Added automatic UI discovery and physical-confirmation pairing.
- Added native Bluetooth adapter and active ESPHome proxy selection through
  Home Assistant, including best-path re-resolution during retry.
- Added automatic Weber Cloud setup for simultaneous Weber app and Home
  Assistant telemetry by default.
- Added exactly four permanent native probe temperature entities; each keeps
  its physical slot number and exposes probe state, type, and battery as
  attributes.
- Added optional probe nicknames that remain visibly tied to permanent probe
  slots and stable unique IDs.
- Kept all four probe slots visible: connected slots show temperature and empty
  slots show `Unknown` with the probe-off icon.
- Removed unvalidated recipe, instruction, status, cavity, timer, and remote
  control entities from the 3.0 release surface.
- Added sequential setup progress, task-specific recovery actions, and grouped
  native settings.
- Added an explicit Home Assistant-only mode with one persistent GATT session
  through Home Assistant's selected local adapter or active ESPHome proxy.
- Made first-session proxy connections prefer Home Assistant's cached GATT
  table, with bounded retry and fresh-discovery recovery for stale caches.
- Added one persistent companion WebSocket for the default Phone + Home
  Assistant mode, with no automatic cross-transport fallback.
- Added quiet cloud reconnection, last-success tracking,
  privacy-minimized diagnostics, and a fixed start-to-start 10-second cadence.
- Added a distinct rejected-credential recovery flow and separated Home
  Assistant internet, Weber service, and hub Wi-Fi troubleshooting.
- Enforced Bluetooth transport length, CRC, and terminal-marker integrity and
  exact source/target routing for cloud status frames.
- Removed cook-history, recipe, instruction, timer, cavity, control, legacy
  migration, user polling, and fallback paths from the 3.0 runtime.
- Added privacy-safe diagnostics, HACS validation, Hassfest, strict typing,
  security scanning, a 95% coverage floor, and Home Assistant config-flow
  tests.
- Renamed the project and repository to **Weber Connect Unofficial**.

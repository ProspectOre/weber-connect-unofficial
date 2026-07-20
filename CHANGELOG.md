# Changelog

## 3.0.0 — Unreleased

- Introduced a native Home Assistant custom integration with automatic device
  discovery and native entities.
- Added automatic UI discovery and physical-confirmation pairing.
- Added native Bluetooth adapter and active ESPHome proxy selection through
  Home Assistant, including best-path re-resolution during retry.
- Isolated 3.0 entity identities from pre-3.0 registry state so fresh defaults
  cannot inherit legacy enabled/disabled choices.
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
- Added opt-in local Bluetooth fallback.
- Added actionable data-loss repairs, last-success tracking, effective-option
  diagnostics, and start-to-start 10-second polling.
- Added privacy-safe diagnostics, HACS validation, Hassfest, strict typing,
  security scanning, a 95% coverage floor, and Home Assistant config-flow
  tests.
- Renamed the project and repository to **Weber Connect Unofficial**.

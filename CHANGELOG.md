# Changelog

## 3.2.0 — 2026-09-05

- Add native **Reconfigure** for owner-initiated same-hub connection replacement,
  preserving existing entities and preventing concurrent replacement flows.
- Recover an established cloud socket once before surfacing a brief dropped
  connection; retain bounded timeout handling and persistent-failure states.
- Include connection/recovery counts and exception types in diagnostics.
- Document targeted Activity filtering for the last-update sensor, including
  the recorder option that also removes future device-page timestamp noise.
- Add reading-status entities for the hub and supported probes, distinguishing
  initial waiting, missing readings, reconnecting, and lost updates without
  guessing whether an unreachable hub is sleeping or a probe is unplugged.
- Show last-update context on sensor attributes during interruptions while
  avoiding timestamp-only history records for unchanged live telemetry.
- Replace removal-based credential repair with same-hub reauthentication that
  preserves the config entry, entity identities, options, and automation references.
- Simplify setup into three progress stages and show nickname settings only for
  baseline and previously discovered probe slots; remove the single-choice
  connection selector and preserve names for hidden slots.

- Require positive review evidence bound to the exact pull-request head before
  the automated merge gate can clear.
- Restrict Weber REST requests and redirects to trusted HTTPS origins and cap
  both compressed and expanded response bodies.
- Release Bluetooth pairing connections on setup errors and cancellation.
- Retire unauthenticated local null-session telemetry; existing
  `home_assistant_only` settings migrate to the authenticated cloud companion.

## 3.1.2 — 2026-08-13

- Add a one-click HACS repository link with installation instructions that work
  both before and after default-store inclusion.
- Add focused discovery, pairing, cloud, availability, diagnostics, and removal
  guidance for end users.
- Refresh stale 3.0-era overview language and point current validation claims
  to the 3.1.1 evidence.
- Add specification-sized normal and high-DPI brand assets while preserving the
  existing project identity and transparent backgrounds.
- This release changes documentation, presentation, and repository metadata
  only; integration runtime behavior is unchanged from 3.1.1.

## 3.1.1 — 2026-08-11

- Preserve hub battery, charging, Wi-Fi, Weber Cloud, and device-state telemetry
  when Weber sends a partial appliance-status frame.
- Retain the last complete hub snapshot across transient cloud socket reconnects
  instead of briefly publishing synchronized `Unknown` entity states, while
  requiring fresh appliance frames before republishing fuel and version data.
- Keep slow-changing hub telemetry visible during longer Weber Cloud outages
  while the connection entity reports disconnected and live cooking and probe
  readings clear as stale.
- Add regression coverage for partial frames, reconnects, sustained failures,
  and recovery, backed by a live Home Assistant soak with no post-baseline hub
  entity dropouts.

## 3.1.0 — 2026-08-11

- Add capability-driven hub battery and charging entities, including the
  battery level requested in issue #42.
- Expose available Wi-Fi, cloud, device, fuel, target, cook-mode, cooking,
  intensity, timer, burner, wireless-probe, and firmware telemetry without
  creating unsupported entities on devices that do not report it.
- Preserve stable entity identities while dynamically adding newly discovered
  capabilities across Weber Cloud and Bluetooth transports.
- Expand diagnostics, documentation, translations, protocol coverage, and
  lifecycle tests for the new production entity surface.

## 3.0.7 — 2026-08-08

- Explain directly on the **No Weber hub found** screen that initial setup
  requires Bluetooth even when Weber Cloud will be used afterward.
- Add the complete recovery path to that screen: close the Weber app, disable
  phone or tablet Bluetooth, wake the hub, and use a nearby Home Assistant
  adapter or active ESPHome proxy with a free connection slot.
- This is setup guidance only; runtime discovery and connection behavior are
  unchanged.

## 3.0.6 — 2026-08-08

- Preserve the cavity display temperature when optional per-unit temperature
  tags are absent, fixing a missing **Grill temperature** entity on affected
  appliances including the Weber EXP325S.
- Prefer the actual cavity temperature and fall back to the display temperature
  only when the actual reading is unavailable, consistently across Bluetooth
  and cloud connections.
- Allow reviewed fork pull requests to satisfy the required review gate while
  continuing to require an explicit maintainer merge.
- Thanks to [@jarnose](https://github.com/jarnose) for reporting the cavity
  temperature issue and contributing the fix in
  [#36](https://github.com/ProspectOre/weber-connect-unofficial/pull/36).

## 3.0.5 — 2026-08-03

- Clarify that pairing approval happens on the physical Weber hub rather than
  in the Weber app.
- Explain how to approve Home Assistant on both the standalone Smart Grilling
  Hub and grill controllers, including the standalone hub's four-probe-light
  prompt and pressable top/display.

## 3.0.4 — 2026-07-28

- Create **Probe 1** and **Probe 2** as the baseline entity set, while adding
  **Probe 3** and **Probe 4** only after the controller reports those slots.
- Preserve stable slot-based entity IDs and normal `Unknown` idle behavior once
  an optional probe slot has been discovered.
- Update the documentation to describe model-aware probe entities instead of a
  fixed four-probe contract.

## 3.0.3 — 2026-07-27

- Added a permanent **Grill temperature** entity for appliances with a built-in
  cavity/ambient sensor, using the current cavity temperature already decoded
  by both cloud and Bluetooth transports.
- Confirmed through a community report that the entity works on the **Weber
  Performer Deluxe Smart 57 / Performer Premium Smart** running firmware
  `2.9.0.8076` in **Phone + Home Assistant** mode.

## 3.0.2 — 2026-07-22

- Added an enabled-by-default **Connection** entity that reports
  Connected/Disconnected, identifies Weber Cloud or Bluetooth, and uses a
  transport-aware icon.
- Added an enabled-by-default **Last successful update** timestamp so routine
  hub sleep or power-off retains useful context about the latest fresh data.
- Remove every retired connection-loss repair on startup, including stale
  repairs left behind by an earlier deleted config entry.
- Kept the established stable probe-entity identities unchanged.

## 3.0.1 — 2026-07-21

- Treat a sleeping, powered-off, or temporarily unreachable hub as normal idle
  behavior instead of raising a Home Assistant repair issue.
- Continue quiet background recovery while retaining registered probe entities
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
- Added stable native probe-temperature entities with physical slot numbers;
  each exposes probe state, type, and battery as attributes.
- Added optional probe nicknames that remain visibly tied to permanent probe
  slots and stable unique IDs.
- Kept registered probe slots visible: connected slots show temperature and
  empty slots show `Unknown` with the probe-off icon.
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

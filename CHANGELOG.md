# Changelog

## 3.2.2 — read-only cloud hotfix

- Remove implicit clock/settings writes and the unused program-details request.
- Enforce empty-payload status and capability reads before cloud connection I/O.
- Preserve stable 3.2.1 pairing, controller handling, entities and dependencies.
- Keep the broader controller-family beta on the separate 3.3.0 release line.
- Use the current hosted CI and canonical manual review process for maintenance.

## 3.2.1 — 2026-09-13

- Add reviewable support reports to setup errors, settings, and credential
  recovery, including versions and bounded diagnostic events without raw logs
  or credentials. Reports can be copied into an existing issue or prefilled
  into a new GitHub issue without restarting setup.
- Include pairing milestones and runtime failure/recovery events; clarify that
  the pairing progress screen can appear before the device receives a request.
- Keep diagnostics available for unloaded entries and redact probe nicknames,
  arbitrary stored options, and raw exception messages from downloads.
- Classify report-capture-only fixes as exact runtime amendments so unchanged
  pairing and cloud transport acceptance does not need to be repeated.

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

Older releases: [3.2.1 changelog](https://github.com/ProspectOre/weber-connect-unofficial/blob/v3.2.1/CHANGELOG.md).

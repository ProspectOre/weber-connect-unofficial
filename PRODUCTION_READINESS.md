# Production readiness

Use the [product matrix](docs/product-compatibility.md) and its candidate-bound
receipts for current qualification status. The versioned sections below retain
historical acceptance and release requirements; passing an older runtime does
not qualify a newer candidate.

## 3.2.2rc1 hardware-validation prerelease

This candidate fixes the confirmed GATT capability and retry defects in issue 65.
Genesis response framing, physical approval, and cloud association remain
unverified. The prerelease validator requires exact version/runtime bindings and
an explicit pending hardware record. Stable releases still require the complete
physical and endurance matrix; prior 3.2.1 receipts are not reused.

See [RC validation and rollback steps](docs/validation/3.2.2rc1-readiness.md).

The current candidate is release-ready only when every automated gate and the
authenticated cloud/pairing matrix pass. Optional compatibility rows are
explicitly marked and do not become claims until exercised. A passing matrix
validates only the documented equipment; it does not certify every Weber model,
firmware, region, adapter, account, router, or ESPHome version.

> **Security boundary update:** runtime null-session Bluetooth telemetry is
> retired by ADR 0003 because the observed frames do not authenticate their
> sender. Proxy-only telemetry rows below are retained as historical 3.0
> availability evidence, not as current release gates or security claims.

## 3.2.1 release candidate

Final review identified two report snapshot defects. Both are fixed with
regression coverage. Because the fixes only affect support-report evidence
capture and return navigation, the prior physical transport and endurance
acceptance is reused through the exact `support_report_evidence_only` runtime
amendment; no pairing protocol or cloud transport changed in runtime
`63bcedc8efc07256cd74cd016477adb826f7c49fc6915966c6fa8a78e693fe1f`.
The amendment still requires the two new targeted regression tests.

### Initial candidate acceptance

Support reports and clarified pairing instructions passed physical acceptance
on Home Assistant 2026.9.1 and an iPhone Air. Same-hub pairing and active
cancellation preserved the device, entity IDs and names. Power loss cleared
readings and reported connection loss; power-on recovered without a reload.
Activity filtering retained status events without timestamp-only noise.

The unchanged runtime completed 3,920 seconds with 393 samples, zero new failed
updates, zero disconnections, and zero capture errors. The maximum update gap
was 11 seconds and maximum sampled age was 2.301 seconds. Final exact-head CI
and review are required before merge and publication. See
[`docs/validation/3.2.1-readiness.md`](docs/validation/3.2.1-readiness.md).

## 3.2 release status

**Physical acceptance passed on the documented equipment.** The reviewed runtime is deployed on Home
Assistant 2026.9.0. It completed a one-hour connection run with six automatic
recoveries and no new failed updates. Same-hub re-pairing preserved the entry
and 18 entities. Owner-controlled power-off cleared readings and reported
connection loss; power-on recovered automatically without a reload.

The final release PR must pass its exact-head CI and review gates before merge
and publication. The candidate-bound records below document physical acceptance.
Track the candidate-bound gates in
[`docs/validation/3.2.0-readiness.md`](docs/validation/3.2.0-readiness.md).
Do not reuse the 3.1.2 presentation-only evidence exception for 3.2: runtime,
pairing-recovery, and entity behavior have changed.

## Current-update acceptance

The reading-status and in-place pairing recovery update requires a physical
Home Assistant UI pass before release. Validate first-update waiting, missing
probe readings, a temporary cloud interruption, sustained loss, and recovery;
confirm retained battery/Wi-Fi values show interruption context. Check setup and
settings on mobile and desktop, including a two-port controller when available.
Exercise same-hub credential replacement and cancellation with named entities
and an existing automation, then confirm entity IDs and references remain intact.
Automated flow tests do not substitute for this hardware and visual acceptance.

## Automated gates

Every release pull request must pass:

- import and config-flow tests on Home Assistant 2026.7;
- pairing, settings, credential-repair, status-frame, malformed-frame,
  persistent cloud session, entity-identity, transport-ownership, and
  diagnostics-redaction tests;
- 100% combined statement and branch coverage across the native
  integration;
- Ruff formatting/lint, strict mypy, Bandit, CodeQL, and Actionlint;
- HACS repository validation and Home Assistant Hassfest;
- runtime dependency vulnerability audit;
- release-contract validation confirming the repository contains only the
  native integration and its release tooling.

## Physical release matrix

The complete [physical matrix](docs/validation/production-physical-matrix.md) preserves
the steps, optional rows, retired telemetry boundaries, and failure behavior.

### Extended compatibility: direct host adapter

See the [matrix row](docs/validation/production-physical-matrix.md#extended-compatibility-direct-host-adapter).

### Historical 3.0 local telemetry: one active ESPHome proxy

See the [matrix row](docs/validation/production-physical-matrix.md#historical-30-local-telemetry-one-active-esphome-proxy).

### Historical 3.0 local telemetry: two active ESPHome proxies

See the [matrix row](docs/validation/production-physical-matrix.md#historical-30-local-telemetry-two-active-esphome-proxies).

### Failure behavior

See the [matrix row](docs/validation/production-physical-matrix.md#failure-behavior).

## Historical release evidence

- [3.1 automated gates and deployment](docs/validation/production-3.1-evidence.md).
- [3.0 equipment, transports, endurance and proxy restart](docs/validation/production-3.0-transports.md).
- [3.0 clean-install failures, investigation and final acceptance](docs/validation/production-3.0-clean-install.md).
- [3.0 follow-up, community report and remaining physical limits](docs/validation/production-3.0-follow-up.md).

### Community compatibility report

See the [version-bound report and unverified scenarios](docs/validation/production-3.0-follow-up.md#community-compatibility-report).

# Production readiness

3.0 is release-ready only when automation and the physical matrix below pass.
A passing matrix validates the documented equipment; it does not certify every
Weber model, firmware, region, adapter, or ESPHome version.

## Automated gates

Every release pull request must pass:

- import and config-flow tests on Home Assistant 2026.7;
- pairing, settings, repair-flow, status-frame, malformed-frame,
  cloud-normalization, live-program, entity-identity, and
  diagnostics-redaction tests;
- at least 95% combined statement and branch coverage across the native
  integration;
- Ruff formatting/lint, strict mypy, Bandit, CodeQL, and Actionlint;
- HACS repository validation and Home Assistant Hassfest;
- runtime dependency vulnerability audit;
- release-contract validation confirming the repository contains only the
  native integration and its release tooling.

## Physical release matrix

### Direct adapter

1. Discover the hub through the Home Assistant host adapter.
2. Fully close the Weber app and temporarily turn off Bluetooth on every phone
   or tablet that uses it.
3. Complete physical-confirmation pairing, then turn Bluetooth back on.
4. Verify four stable probe slots and accurate probe/cavity temperatures.
5. Restart Home Assistant and confirm that no re-pairing is needed.
6. Reopen the Weber app and verify simultaneous phone plus Home Assistant cloud
   telemetry for at least one hour at the 10-second cadence.
7. Start a recipe in the app and compare every populated recipe, instruction,
   target, progress, timer, and temperature entity.
8. Delete the config entry and verify local private data is removed.

### One active ESPHome proxy

Run with the host Bluetooth adapter disabled:

1. Discover and pair through the active proxy.
2. Read direct local probe status through that proxy.
3. Sustain the 10-second cadence for one hour without leaking a connection
   slot.
4. Restart the proxy and verify automatic recovery.
5. Restart Home Assistant and verify automatic recovery.
6. Disable local fallback and verify the phone/cloud default remains intact.

### Extended compatibility: two active ESPHome proxies

This is a non-blocking resilience scenario. No second proxy is available in the
current test environment, so 3.0 does not claim that live connections fail over
between proxies.

Run with the host adapter disabled:

1. Start a local read through proxy A.
2. Make proxy A unavailable while proxy B remains in range.
3. Verify retry re-resolves proxy B without changing entity unique IDs.
4. Restore proxy A and verify Home Assistant may choose either best path.
5. Confirm both proxies release their active connection slots after each read.

### Failure behavior

- Weber Cloud unavailable: entities show the transport failure; Bluetooth is
  used only when local fallback is enabled.
- Sustained update failures: Home Assistant creates one actionable repair and
  clears it automatically after data resumes.
- Proxy out of slots: Home Assistant surfaces unavailable status and retries
  without a restart.
- Hub out of range: the integration releases resources and recovers on a later
  update.
- Pairing rejected or timed out: no config entry or private half-setup remains.
- Home Assistant unload/reload: the persistent cloud socket closes and no
  background task survives the config entry.

## Current evidence

Automated validation passes on Home Assistant 2026.7.2. Matching phone and Home
Assistant cloud readings were observed on a Weber Connect Hub `2.0.3_7398`
with the Weber Android app `2.10.0.2439`. After a full Home Assistant restart,
the native integration resumed cloud telemetry, reported the active probe at
the same temperature, displayed idle recipe fields explicitly, and produced no
Weber log issue.

Discovery and physical-confirmation pairing passed through one active ESPHome
Bluetooth proxy running ESPHome 2026.7.0. Home Assistant identified that proxy
as the connection path. A later production test also received live probe data
with **Data source: Bluetooth** and **Receiving data: Connected** while Home
Assistant reported `Bluetooth Proxy ee608c (08:D1:F9:EE:60:8E)` as the hub's
advertisement source at -48 dBm. The integration then returned successfully to
the default phone-and-cloud mode.

The host adapter was then disabled for a proxy-only endurance run. That run
exposed slow uncached GATT discovery, an incomplete service-cache failure, and
an operation that could remain pending. The corrected implementation now uses
the Home Assistant service cache on the normal path, retries a missing
characteristic once with fresh discovery, preserves the last valid readings
during a transient failure, completes config-entry setup without waiting for a
physical transport, and enforces a 20-second deadline on every local update.
On Home Assistant 2026.7.2, a production restart completed the integration's
config-entry setup in 0.45 seconds; timed-out proxy transactions were recorded
and retried, and no Weber warning or error appeared in the Home Assistant log.

The post-fix one-hour cadence, a proxy restart, and a successful live read after
a Home Assistant restart still need to pass with the hub awake. Those
single-proxy rows remain release blockers. Two-proxy failover is explicitly
untested because a second proxy is not available; it does not block 3.0 and
must not be described as verified.

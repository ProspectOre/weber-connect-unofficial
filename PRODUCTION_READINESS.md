# Production readiness

3.0 is release-ready only when automation and the physical matrix below pass.
A passing matrix validates the documented equipment; it does not certify every
Weber model, firmware, region, adapter, or ESPHome version.

## Automated gates

Every release pull request must pass:

- import and config-flow tests on Home Assistant 2026.7;
- pairing, settings, repair-flow, status-frame, malformed-frame,
  persistent cloud/Bluetooth session, entity-identity, transport-ownership, and
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
4. Verify four stable probe slots and accurate probe temperatures.
5. Restart Home Assistant and confirm that no re-pairing is needed.
6. Reopen the Weber app and verify that one persistent companion WebSocket
   provides simultaneous phone plus Home Assistant telemetry for at least one
   hour at the 10-second cadence.
7. Start a recipe in the app and compare the active probe temperature for the
   full cook without the Home Assistant entity expiring to `Unknown`.
8. Delete the config entry and verify local private data is removed.

### One active ESPHome proxy

Run with the host Bluetooth adapter disabled:

1. Discover and pair through the active proxy.
2. Establish one persistent local GATT session and read direct probe status.
3. Sustain the 10-second cadence for one hour while the same proxy connection
   slot remains owned by the integration.
4. Restart the proxy and verify automatic recovery.
5. Restart Home Assistant and verify automatic recovery.
6. Return to Phone + Home Assistant mode and verify the proxy slot is released
   before the cloud socket starts.

### Extended compatibility: two active ESPHome proxies

This is a non-blocking resilience scenario. No second proxy is available in the
current test environment, so 3.0 does not claim that live connections fail over
between proxies.

Run with the host adapter disabled:

1. Start a local read through proxy A.
2. Make proxy A unavailable while proxy B remains in range.
3. Verify retry re-resolves proxy B without changing entity unique IDs.
4. Restore proxy A and verify Home Assistant may choose either best path.
5. Confirm the selected proxy releases its active connection slot after an
   actual link loss, mode change, or config-entry unload.

### Failure behavior

- Weber Cloud unavailable: the four probe entities remain present and become
  `Unknown` after repeated failures; the integration does not silently take
  Bluetooth from the phone.
- Sustained cloud failures: Home Assistant creates one actionable repair and
  clears it automatically after data resumes.
- Proxy out of slots: Home Assistant retains all four entities as `Unknown` and
  retries without a restart.
- Hub out of range: the integration releases resources and recovers on a later
  update.
- Sleeping local hub: the four probe entities remain visible as `Unknown`; this
  expected idle state does not create a repair.
- Pairing rejected or timed out: no config entry or private half-setup remains.
- Home Assistant unload/reload: the selected cloud socket or GATT session
  closes and no background task survives the config entry.

## Current evidence

The greenfield implementation currently has 99 passing automated tests and
96.25% combined statement/branch coverage against the Home Assistant 2026.7
test framework. Ruff, strict mypy, and whitespace validation pass locally.

Physical testing uses Home Assistant Yellow on Home Assistant 2026.7.2, a Weber
Connect Hub `2.0.3_7398`, the Weber Android app `2.10.0.2439` on a Samsung Galaxy
Tab A9+, and one active ESPHome proxy running ESPHome 2026.7.0. Matching phone
and Home Assistant cloud temperatures, physical-confirmation pairing, proxy
discovery, and direct proxy reads have all been observed on that equipment.

Discovery and physical-confirmation pairing passed through one active ESPHome
Bluetooth proxy running ESPHome 2026.7.0. Home Assistant identified that proxy
as the connection path. A later production test received live probe data while
Home Assistant reported `Bluetooth Proxy ee608c (08:D1:F9:EE:60:8E)` as the
hub's advertisement source at -48 dBm. The integration then returned
successfully to the default phone-and-cloud mode.

On July 19, 2026, the host hci0 entry was disabled and proxy ee608c was the only
active Bluetooth route for an hour-long observation window. ESPHome recorded a
connection to hub `70:91:8F:21:EA:7B`, a successful status read, a normal
`reason=0x00` disconnect, and release of the proxy slot. One transient GATT
`status=133` attempt then recovered automatically on retry. This proves proxy-
only routing, direct protocol compatibility, clean slot release in the tested
prototype, and recovery from that transient controller failure. The hub later
slept, so the window does **not** prove a continuously active 10-second local
cadence.

The production entity check showed exactly four permanent probe-temperature
entities. During an active sample Probe 3 reported `76.1 °F`; after the hub
slept, all four entities remained visible as `Unknown`. Probe state, type, and
battery remain attributes of the temperature entity. hci0 was restored after
the proxy-only window, and the integration was returned successfully to the
recommended Phone + Home Assistant mode.

On July 20, 2026, the final persistent transports were deployed together to
Home Assistant 2026.7.2 and exercised against the same hub and proxy. In
Phone + Home Assistant mode, the companion WebSocket recovered after the
sleeping hub was woken, produced eight successful live updates with zero
consecutive failures, and recorded repeated incoming `0x80` and `0x83` frames.
After the complete proxy test and restoration of the default mode, a fresh
cloud session produced seven successful updates, zero failures, and a live
Probe 1 reading of `22.7 °C`.

For the final persistent proxy smoke test, hci0 was disabled and ESPHome proxy
ee608c was the only active Bluetooth route. Once the Weber app was fully closed
and Bluetooth was disabled on the tablet, ESPHome opened one connection to hub
`70:91:8F:21:EA:7B` at 13:55:25 PDT and retained the active slot without a
disconnect for more than two minutes. Native diagnostics increased from eight
to fourteen successful updates while the same connection remained open;
Probe 1 changed from `23.1 °C` to `23.0 °C`, the failure count did not
increase, and consecutive failures remained zero. A deliberate config-entry
reload released the earlier proxy slot with `reason=0x00`. Before the tablet's
Bluetooth was disabled, competing app ownership caused transient reconnects,
including `reason=0x08`; the integration recovered automatically after the
phone path was released and the hub was woken. This validates the documented
setup instruction as well as proxy-only routing, persistent slot ownership,
fresh status requests, clean unload, and automatic retry on the tested setup.

The test installation was then returned to the recommended state: hci0 enabled,
Phone + Home Assistant selected, exactly four registered entities, no repair
attention, and a healthy cloud transport.

Earlier prototype cloud testing completed 60 minutes 14 seconds with 356
successful updates, zero failures, and a mean interval of approximately 10.15
seconds. All 72 independent Home Assistant availability samples returned HTTP
200, and Home Assistant recorded no Weber warning or error. An active cook then
remained populated for at least 1 hour 47 minutes, including a full Home
Assistant restart, while the app displayed 76°F and Home Assistant 76.1°F.
Those runs validated the companion identity, decoded probe data, stable
entities, and simultaneous app use; they used the retired cook-history polling
path and therefore do **not** validate the final persistent WebSocket lifecycle.

Before release, the final code must still pass the one-hour endurance row on
its persistent companion WebSocket, the continuously-awake one-hour endurance
row on its persistent proxy GATT session, and the proxy/Home Assistant restart
row. The shorter live tests above validate both final transport designs and a
config-entry reload, but are not substitutes for those endurance rows.
Two-proxy failover is explicitly untested because a second proxy is
unavailable; it is a documented non-blocking compatibility scenario and must
not be described as verified.

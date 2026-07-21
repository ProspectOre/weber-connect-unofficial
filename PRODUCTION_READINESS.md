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

The greenfield implementation currently has 104 passing automated tests and
95.90% combined statement/branch coverage against the Home Assistant 2026.7
test framework. Ruff, strict mypy, Bandit, release-contract validation, and
whitespace validation pass locally.

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
active Bluetooth route for an initial observation window. ESPHome recorded a
connection to hub `70:91:8F:21:EA:7B`, a successful status read, a normal
`reason=0x00` disconnect, and release of the proxy slot. One transient GATT
`status=133` attempt then recovered automatically on retry. This established
proxy-only routing, direct protocol compatibility, clean slot release, and
recovery from that transient controller failure before the final endurance
run.

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

The final proxy-only endurance run started at approximately 16:20 PDT on July
20 with the Weber app closed, tablet Bluetooth off, and hci0 disabled. ESPHome
proxy ee608c was the sole active Bluetooth path. Over 61 minutes, native
diagnostics advanced by 350 successful updates and 16 transient failed
attempts. The final sample had zero consecutive failures, no current error, and
a live Probe 2 reading of `23.8 °C`. The proxy's Wi-Fi response time varied and
occasionally missed a ping, but the transport recovered without manual action.

Home Assistant was then restarted while hci0 remained disabled. Its HTTP UI
returned in approximately 26 seconds. After restart, the integration had
already completed nine successful updates, recovered from four transient
attempts, reported zero consecutive failures, and exposed a live Probe 2
reading of `23.2 °C`. No repair, re-pairing, config-entry reload, or manual
transport recovery was required. Home Assistant's Bluetooth page continued to
show proxy ee608c as the sole advertisement path.

The test installation was then returned to the recommended state: hci0 enabled,
Phone + Home Assistant selected, exactly four registered entities, no repair
attention, and a healthy cloud transport.

All four app/cook combinations were exercised after restoration: app closed
with no cook, app open with no cook, app open with an active cook, and app
closed while the cook remained active. Cloud updates continued in every case.
During the active-cook checks, the app and Home Assistant matched after unit
rounding.

The final persistent companion-WebSocket endurance run started at approximately
17:56 PDT with the Weber app open and a recipe active. More than 70 minutes
later, the app still showed the same active cook at `75 °F`; Home Assistant
reported Probe 2 at `23.7 °C` (`74.7 °F`). Final diagnostics showed the socket
connected, 554 successful updates, 15 failed attempts accumulated since the
session began, zero consecutive failures, no current error, and a successful
update less than ten seconds before capture. This validates simultaneous app
use, active-cook temperature continuity, automatic transient recovery, and the
final persistent WebSocket lifecycle on the documented equipment.

Earlier prototype cloud testing completed 60 minutes 14 seconds with 356
successful updates, zero failures, and a mean interval of approximately 10.15
seconds. All 72 independent Home Assistant availability samples returned HTTP
200, and Home Assistant recorded no Weber warning or error. An active cook then
remained populated for at least 1 hour 47 minutes, including a full Home
Assistant restart, while the app displayed 76°F and Home Assistant 76.1°F.
Those runs validated the companion identity, decoded probe data, stable
entities, and simultaneous app use; they used the retired cook-history polling
path and therefore do **not** validate the final persistent WebSocket lifecycle.

The final persistent companion-WebSocket endurance row, proxy-only endurance
row, and proxy-only Home Assistant restart row have passed on the documented
equipment. During the proxy-only run, ESPHome proxy ee608c was also deliberately
rebooted while the integration owned its GATT session. The integration
reconnected without a reload or repair; diagnostics advanced from nine
successful updates and zero failures before the reboot to 31 successful
updates, five transient failures, zero consecutive failures, and no current
error afterward. Probe 2 resumed at `23.8 °C`. This verifies recovery from one
proxy reboot on the documented equipment.

The production config entry was then deleted. Home Assistant removed the entry,
device, all four entities, and stored private configuration. A clean re-add
found the hub through proxy ee608c and completed physical approval, but Weber's
association list never granted the newly generated companion access to the hub,
including after repeated checks beyond five minutes. This failure was retained
as evidence and investigated rather than treated as a successful clean install.

A second clean pairing attempt on July 20 confirmed that first-time pairing is
supported through the active ESPHome proxy. The hub initially advertised but
did not beep or present an approval request. After its probe was unplugged and
the hub button was held for ten seconds to restart it, proxy ee608c established
the connection, the hub beeped, and local pairing returned `CONFIRMED`. The
generated companion again remained absent from Weber's association list. This
separates the two behaviors: proxy-based physical pairing is verified on this
equipment; that run still did not prove clean Weber Cloud association.

Reviewing the proven 2.1 pairing path after those failures found a clean-slate
3.0 ordering regression. Version 2.1 registered the generated cloud companion
before presenting its ID to the hub over Bluetooth, allowing the hub to publish
the association during the approval session. The initial 3.0 config flow paired
locally first and registered the cloud companion afterward, after that one-time
association opportunity had passed. The native flow now restores the proven
cloud-registration-before-Bluetooth order and requires the exact hub to appear
in Weber's association list before saving an entry. A fresh production pairing
with this corrected order completed local approval and the full five-minute
cloud check, but the exact hub remained absent. Immediate isolation testing then
showed that the official app could read the hub and live probe over Bluetooth,
while the same app reported the hub **Offline** as soon as tablet Bluetooth was
disabled. The app showed a saved Wi-Fi network at one signal bar, and the hub's
Wi-Fi MAC was absent from the local ARP table. This run therefore cannot validate
or disprove automatic cloud association: the hub itself had no working cloud
path.

The hub's rear reset was then pressed briefly to reboot it without erasing its
configuration. With tablet Bluetooth disabled, the official Weber app retained
a live Probe 3 reading, proving that the hub's Weber Cloud path had recovered.
A fresh Home Assistant setup then exposed one restart-related ESPHome proxy
edge case: the hub advertised before its complete GATT service table was
available, and Bleak reported a missing Weber session characteristic. The
pairing transport now clears that stale discovery, reconnects up to three times
before presenting the approval request, and reports the exact recovery reason
instead of an unexpected setup failure. Two focused regression tests cover the
successful recovery and bounded failure paths.

The final clean-install run used the corrected cloud-registration-before-BLE
order and kept tablet Bluetooth disabled during pairing. Home Assistant
generated and registered a new private companion, paired through ESPHome proxy
ee608c, received physical approval from the hub, and found the exact hub in
Weber's association list about 12 seconds after the cloud check began. Home
Assistant created the native device without a Weber account login. After tablet
Bluetooth was restored and the official app reopened, the app showed Probe 3 at
`74 °F` while Home Assistant simultaneously showed `73.9 °F`.

Final native diagnostics reported the recommended
`phone_and_home_assistant` mode, `cloud` transport, a connected socket, nine
successful updates, zero failed updates, zero consecutive failures, no current
error, and Probe 3 at `23.3 °C`. The device page contained exactly four stable
probe-temperature entities. This closes the clean-install cloud-association
release blocker on the documented equipment and validates the intended default
setup end to end.

Two-proxy failover remains explicitly untested because a second proxy is not
available. It may not be described as verified.

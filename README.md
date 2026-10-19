# Weber Connect Unofficial

Native Home Assistant support for the Weber Connect Smart Grilling Hub and
compatible built-in Weber Connect controllers. This project is independent of Weber.

The integration uses Bluetooth for one physically confirmed setup and an
authenticated Weber Cloud companion for ongoing temperatures and status. The
Weber app can continue using Bluetooth after setup. No Weber account password,
phone secret, MQTT broker or packet capture is required.

The 3.3.0rc1 beta continues the controller-family work from 3.2.2rc6. It keeps
cloud session requests limited to empty-payload status and capability reads.
Stable 3.2.2 is being prepared separately as a small hotfix from stable 3.2.1.
Use the latest stable HACS release for the established feature set; enable
prereleases only when participating in the hardware trials below.

## Requirements

Home Assistant 2026.7.0 or newer, HACS, internet access and a hub already online
through Wi-Fi/Weber Cloud are required. Setup needs a connectable HA Bluetooth
adapter or an in-range active ESPHome proxy with `bluetooth_proxy.active` enabled
and a free connection slot. No proxy credentials are entered in this integration.

## Install

Install Weber Connect Unofficial through HACS:

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ProspectOre&repository=weber-connect-unofficial&category=integration)

1. Select the button above to open this repository in HACS, then choose
   **Download**. If HACS cannot find the repository while its default-store
   submission is pending, use the manual fallback below.
2. Restart Home Assistant when HACS prompts you.
3. Open **Settings → Devices & services**. Select the discovered Weber hub, or
   choose **Add integration → Weber Connect Unofficial**.
4. Before closing the Weber app, turn off Bluetooth on that phone or tablet and
   confirm the hub still appears online through Wi-Fi. Leave Bluetooth off.
   Initial setup always needs Home Assistant internet access and a working
   hub-to-Weber Cloud connection.
5. Fully close the Weber app on every phone or tablet that uses it, and turn off
   Bluetooth on any other one. This prevents a phone from reclaiming the hub
   while Home Assistant pairs.
6. Wake the hub and continue setup. Approval happens on the physical hub, not
   in the Weber app. On a standalone Smart Grilling Hub, wait for all four
   probe indicators to light, then press down once on the top/display within
   60 seconds. On a grill controller, press its dial or confirmation control
   when prompted.
7. Home Assistant checks Weber Cloud for up to five minutes. After setup
   completes, turn Bluetooth back on and reopen the Weber app.

### Manual HACS fallback

If HACS cannot find the repository while its default-store submission is pending,
add `https://github.com/ProspectOre/weber-connect-unofficial` under **HACS →
Integrations → ⋮ → Custom repositories**, category **Integration**, then download
and restart Home Assistant.

## Updates and recovery

Keep existing integration entries and entities when updating. After a download,
restart Home Assistant and check the integration version in **Download diagnostics**.
If a private companion credential needs replacement, use the integration's
**Reconfigure** or reauthentication flow for the same hub. This preserves entity
identities and automation references. Pairing approval happens on the physical hub.

## Scope and support

Sensors expose reported grill/probe temperatures, supported status, connection
health and last-update context. Cloud telemetry needs internet access and the
hub's Wi-Fi/cloud connection. An offline hub or an unplugged probe can legitimately
show unknown readings. Local telemetry without authenticated peer setup is disabled.

The integration sends no ignition, cooking-program, target-temperature, Wi-Fi or
mode-control commands. Pairing compatibility depends on the exact controller;
other product families are not qualified merely by being discovered.

For a failure, choose **Get help / report a problem**, review the generated report,
and share it in the existing issue. Please exclude credentials, QR codes, private
identifiers and raw packets. See [architecture](ARCHITECTURE.md), [security](SECURITY.md), and the
[3.3.0rc1 validation record](docs/validation/3.3.0rc1-readiness.md).

Published historical installation and hardware evidence remains available in the
[3.2.1 documentation](https://github.com/ProspectOre/weber-connect-unofficial/blob/v3.2.1/README.md).

## Beta compatibility

The [product matrix](docs/product-compatibility.md) separates source support from
version-bound hardware results. This beta includes controller-family routing,
SiLabs setup work, reported slots above four and SmokeFire fuel/status freshness
changes. Genesis and SiLabs interoperability, portable accessories and other
listed families still need exact-device results. Direct portable Bluetooth
telemetry remains disabled until peer authentication is established. Longer
SmokeFire cooking-time cloud gaps remain unresolved.

RC6's targeted app-closed telemetry and automatic recovery trial is historical
evidence, recorded in its [live receipt](docs/validation/3.2.2rc6-live-observation.json)
and [published release](https://github.com/ProspectOre/weber-connect-unofficial/releases/tag/v3.2.2rc6).
It does not establish the new candidate's full physical acceptance.

Detailed behavior, Activity filtering and troubleshooting are preserved in the
[RC6 user guide](https://github.com/ProspectOre/weber-connect-unofficial/blob/v3.2.2rc6/README.md#everyday-behavior).

## Privacy and removal

Generated private companion credentials stay in the HA config entry. Support
reports use an allowlist and are shared only when you open and submit them.
Remove the integration entry under Devices & services, then remove it in HACS
and restart HA. This removes its entities; it does not reset the Weber app.

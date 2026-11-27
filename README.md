# Weber Connect Unofficial

Native Home Assistant support for the Weber Connect Smart Grilling Hub and
compatible built-in Weber Connect controllers. This project is independent of Weber.

The integration uses Bluetooth for one physically confirmed setup and an
authenticated Weber Cloud companion for ongoing temperatures and status. The
Weber app can continue using Bluetooth after setup. No Weber account password,
phone secret, MQTT broker or packet capture is required.

## Choose a release

| Channel | Use it for | What's available |
| --- | --- | --- |
| [Stable 3.2.2](https://github.com/ProspectOre/weber-connect-unofficial/releases/tag/v3.2.2) | Everyday use | Physically approved Bluetooth setup and read-only Weber Cloud readings on supported controllers. The original Smart Grilling Hub is the main supported device. |
| [Beta 3.3.0rc14](https://github.com/ProspectOre/weber-connect-unofficial/releases/tag/v3.3.0rc14) | Opt-in hardware testing | Updated setup for built-in controllers, read-only probe targets when reported, and a fix for SmokeFire fuel going unknown between reports. One EP thermometer completed USB setup and resumed fresh readings after restart. |

Stable 3.3.0 is waiting on the physical acceptance checks, including a
one-hour Connect Hub run with no update gap over 30 seconds. See
[all published releases](https://github.com/ProspectOre/weber-connect-unofficial/releases)
and the [tested models and firmware](https://github.com/ProspectOre/weber-connect-unofficial/blob/main/docs/product-compatibility.md).

## Requirements

Home Assistant 2026.7.0 or newer, HACS, internet access and a hub already online
through Wi-Fi/Weber Cloud are required. Setup needs a connectable HA Bluetooth
adapter or an in-range active ESPHome proxy with `bluetooth_proxy.active` enabled
and a free connection slot. No proxy credentials are entered in this integration.

## Install

Install Weber Connect Unofficial through HACS:

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ProspectOre&repository=weber-connect-unofficial&category=integration)

1. Select the button above to open this repository in HACS, then choose
   **Download**. If HACS cannot find the repository, use the manual fallback below.
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

If HACS cannot find the repository,
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

The integration reads reported state. Operate the grill and adjust cook settings
on the grill or in the Weber app. Direct Bluetooth telemetry from portable
probes remains disabled. See [architecture](ARCHITECTURE.md) and
[security](SECURITY.md) for the integration's data and control boundaries.

## Help test or report a problem

Keep your working entry. During normal use, note whether readings change,
reconnect on their own, and return after a Home Assistant restart. Share the
exact grill or thermometer model, firmware, integration and Home Assistant
versions, and Bluetooth adapter or proxy. Successful and partial results help.

If setup fails, choose **Get help / report a problem** and note the last step
that worked. For a reading gap, note the time and time zone, whether the Weber
app still had fresh readings, and whether Home Assistant recovered on its own.
Review diagnostics before sharing and remove private details.

See [how to install or roll back the beta, what to check on your model, and where to post](https://github.com/ProspectOre/weber-connect-unofficial/blob/main/docs/support/hardware-trials.md).

## Privacy and removal

Generated companion credentials stay in Home Assistant. You choose whether to
share a support report.
Remove the integration entry under Devices & services, then remove it in HACS
and restart HA. This removes its entities; it does not reset the Weber app.

Weber may retain an unused server-side companion record after cancellation or
removal. Registration precedes physical approval, and no supported revocation
endpoint is available; removing the entry deletes its local credential only.

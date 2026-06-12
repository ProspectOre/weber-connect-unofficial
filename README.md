# Weber Connect Home Assistant Add-ons

Unofficial Home Assistant add-ons for local Weber Connect telemetry.

The first add-on in this repository is `weber_connect_ble`, a local BLE bridge
that reads Weber Connect Hub probe status and publishes the data to Home
Assistant through MQTT discovery.

## Add-on

| Add-on | Purpose | Status |
| --- | --- | --- |
| Weber Connect BLE Bridge | Publishes Weber Connect probe temperature, state, and battery sensors over MQTT | Experimental public release |

## Install

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FProspectOre%2Fweber-connect-home-assistant-addon)

1. Click the button above, or in Home Assistant open **Settings > Apps**,
   choose **Install app**, then choose **Repositories** from the overflow menu
   and add:

   ```text
   https://github.com/ProspectOre/weber-connect-home-assistant-addon
   ```

   On older Home Assistant versions, the same area may be labeled
   **Settings > Add-ons > Add-on Store** instead of **Apps**.

2. Install **Weber Connect BLE Bridge** and start it.
3. Click **Open Web UI** and tap **Find My Hub**.
4. When the hub beeps, **press the button on the hub** to confirm pairing.

The panel discovers your hub, pairs with it, and starts publishing probe
sensors automatically. No configuration is required.

When you need the Weber phone app to connect, tap **Use with Phone** in the
panel. The add-on releases the hub for the Weber app and reconnects on its own
when the handoff window ends.

## Requirements

- Home Assistant OS, Supervised, or another installation type that supports add-ons.
- A Bluetooth adapter available to Home Assistant.
- The MQTT integration and an MQTT broker, such as the Mosquitto broker add-on.

## Privacy And Scope

This bridge talks to the hub locally over BLE and publishes only local telemetry
to MQTT. It does not use Weber cloud credentials, does not send data to a
third-party service, and does not issue control commands to the grill.

Private pairing summaries, pairing keys, app captures, MQTT passwords, and
runtime JSON output are intentionally excluded from this repository.

## Documentation

See [weber_connect_ble/DOCS.md](weber_connect_ble/DOCS.md) for setup,
configuration, troubleshooting, and release notes.

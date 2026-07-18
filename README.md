# Weber Connect for Home Assistant (Unofficial)

Unofficial Home Assistant add-ons for Weber Connect telemetry.

**Weber Connect for Home Assistant** is an unofficial add-on that pairs directly
with a Weber Connect Hub, publishes four stable probe slots through MQTT
discovery, and provides a built-in one-screen Home Assistant control center for
setup and Weber app access. Probe slots can have optional nicknames while always
retaining their physical probe number.
BLE remains the preferred local transport. The recommended first-run setup also
creates a bridge-owned Weber Cloud companion so telemetry keeps flowing while
the official Weber app owns Bluetooth.

## Add-on

| Add-on | Purpose | Status |
| --- | --- | --- |
| Weber Connect for Home Assistant (Unofficial) | Probe temperature, state, and battery sensors through MQTT discovery | Stable BLE-first monitoring with Weber app access by default |

## Install

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FProspectOre%2Fweber-connect-home-assistant-addon)

1. Click the button above, or open **Settings > Apps**, choose **Install app**,
   open **Repositories**, and add:

   ```text
   https://github.com/ProspectOre/weber-connect-home-assistant-addon
   ```

   Older Home Assistant versions may label this area **Settings > Add-ons >
   Add-on Store**.
2. Install and start **Weber Connect for Home Assistant (Unofficial)**.
3. Fully close the Weber app on every nearby phone or tablet so it is not
   connected to the hub over Bluetooth.
4. Open the add-on Web UI and select **Set Up My Hub**.
5. Press the hub button when it beeps to confirm pairing.

The recommended path registers a private bridge companion with Weber Cloud,
pairs that same identity with the hub, and publishes the probe entities
automatically. It needs one setup action and one physical confirmation. Users
who do not want cloud access can select **Local only** instead.

## Use Home Assistant And The Weber App Together

The hub accepts one active BLE client, so Weber app access is the default
onboarding path. It solves that limitation without copying a phone secret or
asking for a Weber account password. On a fresh installation, select **Set Up
My Hub**, confirm on the hub, and allow up to five minutes for Weber's backend
to publish the association. For an older or local-only installation, open
**Settings > Weber app + Home Assistant** and select **Set up Weber app access**.

The bridge creates and registers its own random companion identity, pairs that
same identity with the hub over BLE, and stores it privately for that add-on
installation. Setup does not depend on Android packet capture, certificate
interception, phone app storage, or email/password login.

While the Weber app is using Bluetooth, it can display the hub and start a
recipe while Home Assistant continues receiving the hub's cloud probe
telemetry. Home Assistant also creates entities for cavity temperatures, active
recipe details, instructions, cook progress, and timers. Those richer fields
populate only when Weber makes the corresponding live-session data available
to the bridge companion. Optional remote controls are disabled by default and
remain experimental until verified on additional hardware.

New installs refresh local probe readings every 10 seconds. While the Weber app
has Bluetooth access, cloud-ready bridges preselect **Manual reconnect** so Home
Assistant can keep following the cook without an arbitrary deadline; otherwise
the saved timed fallback is used.

## Requirements

- Home Assistant OS, Supervised, or another installation with add-on support.
- A Bluetooth adapter available to Home Assistant.
- The MQTT integration and a broker such as the Mosquitto broker add-on.
- Internet access for the recommended Weber app access path; **Local only**
  remains available without it.

Bluetooth is direct from the add-on container to the Home Assistant host's
BlueZ service. Home Assistant Bluetooth proxies are not used by this add-on, so
initial pairing and local BLE reads require the hub to be near the Home
Assistant machine or its attached Bluetooth adapter.

## Compatibility And Validation

The current build was physically tested with a Weber Connect Hub running
`2.0.3_7398`, Home Assistant Yellow running Home Assistant `2026.7.2`, and the
official Weber app `2.10.0.2439` on a Samsung Galaxy Tab A9+ (`SM-X210`, Android
16). The official app remained connected over Bluetooth while Home Assistant
received matching Probe 2 temperatures through Weber Cloud. A T-Bone Steak
recipe started in the app continued producing current Home Assistant probe
telemetry. The add-on's complete MQTT discovery set was also accepted by Home
Assistant and updated at the configured 10-second cadence.

On that same setup, Weber did not return live recipe/program details to the
independently paired bridge companion, so recipe title, instructions, remote
cook commands, and timer commands are implemented but **not physically
verified**. The official app logged that it could not fetch the cook-program
details as well. The build is covered by 331 automated tests with a 95% coverage
gate.

That is the project's current test matrix, not a claim that every Weber model,
firmware version, Home Assistant host, Bluetooth adapter, or region has been
certified. The BLE protocol and Weber's private cloud API may vary. If another
combination behaves differently, please open an issue or pull request and
include the non-sensitive environment details listed in
[CONTRIBUTING.md](CONTRIBUTING.md). Community compatibility reports are how the
documented matrix will grow.

## Privacy And Scope

BLE readings stay local between Home Assistant and the hub. The recommended
phone-coexistence setup also uses Weber's private, undocumented API; **Local
only** is available during onboarding. The cloud path sends companion
authentication, live session, program-detail, and cook-history requests. When
the user explicitly enables remote controls, it may also confirm or stop an
active cook and start or reset timers. It never configures Wi-Fi, installs or
starts a recipe, changes a temperature target, ignites an appliance, or changes
a grill mode.

Pairing keys, cloud device passwords and tokens, MQTT passwords, app captures,
and runtime JSON are private runtime data and are excluded from the repository.

## Support

This project is not affiliated with, endorsed by, or supported by Weber. The
private cloud API can change without notice. Report bugs through the repository
issue tracker and security problems through GitHub's private vulnerability
reporting flow.

## Documentation

- [Full setup and troubleshooting](weber_connect_ble/DOCS.md)
- [Architecture](ARCHITECTURE.md)
- [Security policy](SECURITY.md)
- [Changelog](weber_connect_ble/CHANGELOG.md)
- [GitHub wiki](https://github.com/ProspectOre/weber-connect-home-assistant-addon/wiki)

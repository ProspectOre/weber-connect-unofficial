# Weber Connect Unofficial

Native Home Assistant support for the Weber Connect Smart Grilling Hub.

Version 3.0 is one native Home Assistant integration:

- automatic Bluetooth discovery through local adapters and active ESPHome proxies;
- one physically confirmed setup with no Weber email, password, phone secret, or packet capture;
- native devices and entities—no MQTT broker or separate control panel;
- four stable probe slots, cavity temperatures, timers, active recipe, current
  instruction, target, mode, and cook progress;
- phone + Home Assistant by default: the Weber app may own Bluetooth while
  Home Assistant follows the cook through its own Weber Cloud connection;
- optional local Bluetooth fallback and narrowly allowlisted active-cook
  controls.

This project is not affiliated with, endorsed by, or supported by Weber.

> [!IMPORTANT]
> 3.0 is under active development and has not been released yet. The native
> code and automated Home Assistant 2026.7 tests are in place. Real-hardware
> setup and direct local readings through one active ESPHome proxy are
> verified. Proxy-only startup and bounded failure recovery are also verified;
> the final post-fix live recovery and endurance run remains a release blocker.
> Multi-proxy failover is not tested and is not claimed.

## Install

3.0 will be installed as a HACS custom integration:

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add this repository as category **Integration**:

   ```text
   https://github.com/ProspectOre/weber-connect-unofficial
   ```

3. Download **Weber Connect Unofficial** and restart Home Assistant.
4. Open **Settings → Devices & services**. Select the discovered Weber hub, or
   choose **Add integration → Weber Connect Unofficial**.
5. Fully close the Weber app on every phone or tablet that uses it, then
   temporarily turn off Bluetooth on those devices. This prevents a phone from
   reclaiming the hub while Home Assistant pairs.
6. Wake the hub, continue setup, and approve Home Assistant on the hub display.
7. After setup completes, turn Bluetooth back on and reopen the Weber app.

Home Assistant creates and stores its own Weber connection automatically.

## Everyday behavior

The default mode is **phone + Home Assistant**. Home Assistant reads through
Weber Cloud every 10 seconds, leaving the hub's single Bluetooth connection
available to the Weber app. A recipe started in the Weber app can populate the
native recipe, instruction, target, progress, temperature, and timer entities
when Weber makes that live-session data available to Home Assistant.

Home Assistant uses Bluetooth for initial pairing. If **Local Bluetooth
fallback** is enabled in the integration options, it can also read locally
when cloud updates fail. Local reads may temporarily take the hub away from the
phone. Home Assistant chooses the best reachable local adapter or active proxy
for every connection attempt and can choose a different path on retry.

Probe entities retain stable slot IDs such as `probe_2_temperature`. Optional
nicknames keep the physical number visible—for example, **Brisket · Probe 2**—
without changing the entity's identity.

The device page starts with the entities most people need: four permanent probe
slots, detected probe temperatures, the active recipe, the current instruction,
and connection status. An empty slot reads **Not connected**. Numeric temperature
and battery entities are added automatically after a probe's first reading so
Home Assistant never presents a missing value as a live measurement. Batteries,
cavities, timers, cook details, and diagnostics remain available as disabled
entities for users who want them.

Idle recipe and instruction entities say that no cook is active. The separate
receiving-data entity shows whether the integration itself is online.

Remote controls are off by default. When enabled, 3.0 can confirm or stop an
already-active cook and reset an existing timer. It cannot ignite a grill,
configure Wi-Fi, install or start a recipe, change a target, or change grill
mode.

## Requirements

- Home Assistant 2026.7.0 or newer.
- HACS for installation until the integration is accepted into Home Assistant.
- A connectable Home Assistant Bluetooth adapter or active ESPHome Bluetooth
  proxy in range during setup.
- Internet access for the default **Phone + Home Assistant** mode.

For an ESPHome proxy, `bluetooth_proxy.active` must be enabled and a connection
slot must be available. No proxy address or encryption key is entered into this
integration; Home Assistant owns adapter selection and credentials.

## Compatibility and validation

The cloud data path was physically tested on a Weber Connect Hub running
`2.0.3_7398`, Home Assistant Yellow on Home Assistant `2026.7.2`, and Weber app
`2.10.0.2439` on a Samsung Galaxy Tab A9+ (`SM-X210`, Android 16). Matching
probe readings continued through Weber Cloud while the phone owned Bluetooth.
The same hub was also discovered and paired through an active ESPHome Bluetooth
proxy running ESPHome `2026.7.0`; Home Assistant identified that proxy as the
connection path during setup. A later local-only production test received live
probe data over that proxy and then returned cleanly to the default cloud mode.

For 3.0, Home Assistant 2026.7.2 import, config flow, identity generation,
entity contracts, protocol frames, cloud normalization, and adapter re-selection
are automated. Proxy discovery, pairing, and direct readings are verified on
the equipment above. With the host adapter disabled, production validation also
verified sub-second config-entry setup and bounded retries when a proxy
transaction stalls. The remaining post-fix live recovery and endurance cases
in [Production readiness](PRODUCTION_READINESS.md) must pass before release.
Multi-proxy failover remains an explicitly unverified compatibility scenario.

That is a test matrix, not a claim that every Weber model, firmware, account
region, or proxy has been certified. Compatibility reports and pull requests
are welcome; see [Contributing](CONTRIBUTING.md) for the safe details to include.

## Privacy

The integration generates a random companion ID, device password, and key
material. Home Assistant stores them in the config entry; diagnostics redact
them and all hub/companion identifiers. The integration never asks for the user's Weber account password and
does not copy secrets from the official app.

Weber Cloud is private and undocumented. The default mode sends Home
Assistant's generated identity and cook-session requests to Weber. **Home
Assistant only** mode avoids those requests but cannot share the hub's single
Bluetooth connection with the phone.

## Project documents

- [Architecture](ARCHITECTURE.md)
- [Production readiness](PRODUCTION_READINESS.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [GitHub wiki](https://github.com/ProspectOre/weber-connect-unofficial/wiki)

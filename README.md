# Weber Connect Unofficial

Native Home Assistant support for the Weber Connect Smart Grilling Hub.

Version 3.0 is one native Home Assistant integration:

- automatic Bluetooth discovery through local adapters and active ESPHome proxies;
- one physically confirmed setup with no Weber email, password, phone secret, or packet capture;
- native devices and entities—no MQTT broker or separate control panel;
- exactly four stable probe temperature entities—one for each physical slot;
- phone + Home Assistant by default: the Weber app may own Bluetooth while
  Home Assistant follows probe temperatures through its own Weber Cloud
  connection;
- an optional Home Assistant-only mode that owns one local Bluetooth connection
  through Home Assistant's adapter or active ESPHome proxy selection.

This project is not affiliated with, endorsed by, or supported by Weber.

> [!IMPORTANT]
> 3.0 is under active development and has not been released yet. The native
> code and automated Home Assistant 2026.7 tests are in place. Local pairing,
> a 70-minute simultaneous app/cloud session using an already provisioned
> companion, a one-hour proxy-only session, a proxy reboot, and a Home Assistant
> restart using only that proxy have passed on the equipment below. A clean
> install did **not** receive access to the hub in Weber Cloud after local
> pairing and repeated checks beyond five minutes. Universal Phone + Home
> Assistant setup is therefore not yet proven and blocks release. A second proxy
> was not available, so multi-proxy failover is also unverified.

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
7. Home Assistant checks Weber Cloud for up to five minutes. After setup
   completes, turn Bluetooth back on and reopen the Weber app.

The intended setup creates and stores a private Home Assistant companion
without asking for a Weber account password. Clean-install cloud association is
still under validation and 3.0 will not be released until that path is proven.

## Everyday behavior

After cloud association succeeds, the default mode is **Phone + Home
Assistant**. Home Assistant keeps one Weber Cloud companion socket open and
requests fresh status on a 10-second cadence, leaving the hub's single
Bluetooth connection available to the Weber app. Recipes continue to be
started and managed in the Weber app while Home Assistant monitors the four
probe temperature slots.

**Home Assistant only** instead keeps one local GATT connection open through
Home Assistant's selected adapter or active ESPHome proxy. It reconnects only
after a real link loss. This mode cannot share the hub's Bluetooth connection
with the Weber app. There is no automatic fallback between cloud and Bluetooth:
changing modes closes one transport before starting the other.

Probe entities retain stable slot IDs such as `probe_2_temperature`. Optional
nicknames keep the physical number visible—for example, **Brisket · Probe 2**—
without changing the entity's identity.

The device page has exactly one permanent temperature entity for each physical
slot: **Probe 1** through **Probe 4**. A connected probe shows its temperature;
an empty slot—or a sleeping hub with no current reading—reads **Unknown** with
the probe-off icon. That is the normal idle state, not a sign that the
integration or Home Assistant is offline. Battery level, probe type, and probe
state remain attributes on that same entity instead of creating redundant
entities.

3.0 is deliberately read-only. Recipe text, instructions, cook controls,
cavities, timers, and technical connection-status entities are not exposed.

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

Testing uses a Weber Connect Hub running `2.0.3_7398`, Home Assistant Yellow on
Home Assistant `2026.7.2`, Weber app `2.10.0.2439` on a Samsung Galaxy Tab A9+
(`SM-X210`, Android 16), and one ESPHome Bluetooth proxy running ESPHome
`2026.7.0`. This equipment has demonstrated physical-confirmation pairing,
matching phone and cloud temperatures with an already provisioned companion,
proxy discovery, direct proxy reads, and recovery after a deliberate proxy
reboot. It has not yet demonstrated clean-install cloud association for a newly
generated companion.

The current greenfield transport implementation has 100 automated tests and
96.27% combined statement/branch coverage. Import, config flow, transient
identity generation, entity contracts, protocol frames, persistent-session
reuse, reconnect behavior, proxy service-cache recovery, diagnostics redaction,
and transport ownership are covered. Live smoke and config-entry reload tests
now cover both the persistent WebSocket and persistent proxy-GATT lifecycles.
The final persistent cloud test ran for more than 70 minutes with the Weber app
open and an active cook. The proxy-only test ran for more than one hour and was
followed by a successful Home Assistant restart without re-pairing. See
[Production readiness](PRODUCTION_READINESS.md) for the measurements and
remaining unverified scenarios. Multi-proxy failover is explicitly unverified.

That is a test matrix, not a claim that every Weber model, firmware, account
region, or proxy has been certified. Compatibility reports and pull requests
are welcome; see [Contributing](CONTRIBUTING.md) for the safe details to include.

## Privacy

The integration generates a random companion ID, cloud device password, and
transient pairing key material. Only the approved companion identity and cloud
credential are stored in the config entry; the pairing keys are discarded.
Diagnostics redact stored credentials and all hub/companion identifiers. The
integration never asks for the user's Weber account password and does not copy
secrets from the official app.

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

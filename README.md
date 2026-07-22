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
> On the equipment below, a clean 3.0 installation generated its own private
> companion, paired through an ESPHome
> proxy, appeared in Weber Cloud in about 12 seconds, and immediately delivered
> native probe entities while the Weber app was open. The 70-minute app/cloud
> session, one-hour proxy-only session, proxy reboot, and proxy-only Home
> Assistant restart also passed. A second proxy was not available, so
> multi-proxy failover remains explicitly unverified.

## Install

3.0 is installed as a HACS custom integration:

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add this repository as category **Integration**:

   ```text
   https://github.com/ProspectOre/weber-connect-unofficial
   ```

3. Download **Weber Connect Unofficial** and restart Home Assistant.
4. Open **Settings → Devices & services**. Select the discovered Weber hub, or
   choose **Add integration → Weber Connect Unofficial**.
5. Before closing the Weber app, turn off Bluetooth on that phone or tablet and
   confirm the hub still appears online through Wi-Fi. Leave Bluetooth off.
   Initial setup always needs Home Assistant internet access and a working
   hub-to-Weber Cloud connection, including when you intend to select **Home
   Assistant only** after setup.
6. Fully close the Weber app on every phone or tablet that uses it, and turn off
   Bluetooth on any other one. This prevents a phone from reclaiming the hub
   while Home Assistant pairs.
7. Wake the hub, continue setup, and approve Home Assistant on the hub display.
8. Home Assistant checks Weber Cloud for up to five minutes. After setup
   completes, turn Bluetooth back on and reopen the Weber app.

The intended setup creates and stores a private Home Assistant companion
without asking for a Weber account password. The documented clean-install path
has been validated end to end on the equipment below.

### Replacing the 2.1 add-on

3.0 is a clean native integration, not an in-place add-on upgrade. In the 2.1
panel, use **Forget This Hub**, then stop and uninstall the add-on before
installing 3.0. The native integration creates a new device and four native
sensor entities; it does not import the add-on's MQTT entities or settings. If
an old unavailable MQTT device remains, remove its retained discovery records
from the broker and delete that MQTT device from Home Assistant. The add-on and
its MQTT broker are not needed by 3.0.

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
an empty slot—or a sleeping or powered-off hub with no current reading—reads
**Unknown** with the probe-off icon. That is the normal idle state, not a sign
that the integration or Home Assistant is offline. Routine disconnects recover
quietly without raising a Home Assistant repair. Battery level, probe type, and
probe state remain attributes on that same entity instead of creating
redundant entities.

3.0 is deliberately read-only. Recipe text, instructions, cook controls,
cavities, timers, and technical connection-status entities are not exposed.

## Requirements

- Home Assistant 2026.7.0 or newer.
- HACS for installation until the integration is accepted into Home Assistant.
- A connectable Home Assistant Bluetooth adapter or active ESPHome Bluetooth
  proxy in range during setup.
- Home Assistant internet access and a hub that is already online in Weber
  Cloud for every initial installation. **Home Assistant only** avoids cloud
  traffic after setup, not during setup.

For an ESPHome proxy, `bluetooth_proxy.active` must be enabled and a connection
slot must be available. No proxy address or encryption key is entered into this
integration; Home Assistant owns adapter selection and credentials.

## Compatibility and validation

Testing uses a Weber Connect Hub running `2.0.3_7398`, Home Assistant Yellow on
Home Assistant `2026.7.2`, Weber app `2.10.0.2439` on a Samsung Galaxy Tab A9+
(`SM-X210`, Android 16), and one ESPHome Bluetooth proxy running ESPHome
`2026.7.0`. This equipment has demonstrated physical-confirmation pairing,
clean-install cloud association for a newly generated companion, matching phone
and Home Assistant temperatures, proxy discovery, direct proxy reads, and
recovery after a deliberate proxy reboot.

The final 3.0 physical setup and endurance tests used the ESPHome proxy path.
A host-adapter-only pairing and endurance run has not been completed, so direct
adapter compatibility is implemented through Home Assistant's standard
Bluetooth manager but is not claimed as physically verified for this release.

The final candidate was also restarted into **Home Assistant only** with the
ESPHome proxy as the sole Bluetooth source. After a deliberate proxy power
cycle, native diagnostics advanced from 10 to 17 successful updates without
another failed update, retained the live `23.1 °C` probe reading, and reported
no current error. Returning to **Phone + Home Assistant** then produced six
cloud updates with zero failures while the official app was open.

The current greenfield transport implementation is held to at least 95%
combined statement/branch coverage. Import, config flow, transient
identity generation, entity contracts, protocol frames, persistent-session
reuse, reconnect behavior, proxy service-cache recovery, diagnostics redaction,
and transport ownership are covered. Live smoke and config-entry reload tests
now cover both the persistent WebSocket and persistent proxy-GATT lifecycles.
The final persistent cloud test ran for more than 70 minutes with the Weber app
open and an active cook. The proxy-only test ran for more than one hour and was
followed by a successful Home Assistant restart without re-pairing. See
[Production readiness](PRODUCTION_READINESS.md) for the measurements and
remaining unverified scenarios. The corresponding
[redacted machine-readable evidence](docs/validation/3.0.0-rc-physical.json)
contains no device identifiers. Multi-proxy failover is explicitly unverified.

That is a test matrix, not a claim that every Weber model, firmware, account
region, or proxy has been certified. Compatibility reports and pull requests
are welcome; see [Contributing](CONTRIBUTING.md) for the safe details to include.

## Privacy

The integration generates a random companion ID, cloud device password, and
transient pairing value. Only the approved companion identity and cloud
credential are stored in the config entry; the pairing value is discarded.
Diagnostics redact stored credentials and all hub/companion identifiers. The
integration never asks for the user's Weber account password and does not copy
secrets from the official app.

Weber Cloud is private and undocumented. The default mode sends Home
Assistant's generated identity and read-only current-status requests to Weber.
**Home Assistant only** mode avoids those requests but cannot share the hub's
single Bluetooth connection with the phone.

Registering the private companion happens before physical approval. If setup is
abandoned after registration, Weber may retain an unused server-side companion
record; it contains no Weber account password, and Weber provides no supported
revocation endpoint. Removing the Home Assistant entry always deletes the local
credential.

## Project documents

- [Architecture](ARCHITECTURE.md)
- [ADR 0001: superseded proxy relay](docs/adr/0001-home-assistant-bluetooth-proxy-transport.md)
- [ADR 0002: native transport lifecycle](docs/adr/0002-native-transport-lifecycle.md)
- [Production readiness](PRODUCTION_READINESS.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [GitHub wiki](https://github.com/ProspectOre/weber-connect-unofficial/wiki)

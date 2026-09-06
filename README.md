# Weber Connect Unofficial

<p align="center">
  <img src="images/logo.png" srcset="images/logo@2x.png 2x" alt="Weber Connect Unofficial" width="320">
</p>

Native Home Assistant support for the Weber Connect Smart Grilling Hub and
compatible built-in Weber Connect controllers.

Version 3.x is one native Home Assistant integration:

- automatic Bluetooth discovery through local adapters and active ESPHome proxies;
- one physically confirmed setup with no Weber email, password, phone secret, or packet capture;
- native devices and entities—no MQTT broker or separate control panel;
- model-aware temperature entities for the built-in grill sensor and physical
  probe slots the controller reports, plus clear connection and last-update
  context;
- phone + Home Assistant by default: the Weber app may own Bluetooth while
  Home Assistant follows probe temperatures through its own Weber Cloud
  connection.

This project is not affiliated with, endorsed by, or supported by Weber.

> [!IMPORTANT]
> On the equipment below, a clean 3.0 installation generated its own private
> companion, paired through an ESPHome
> proxy, appeared in Weber Cloud in about 12 seconds, and immediately delivered
> native probe entities while the Weber app was open. The 70-minute app/cloud
> session also passed. Local Bluetooth is used for physically confirmed setup
> only; unauthenticated null-session telemetry is rejected.

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

Until the default-store submission is accepted, the repository can always be
added manually:

1. Open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add this repository as category **Integration**:

   ```text
   https://github.com/ProspectOre/weber-connect-unofficial
   ```

3. Download **Weber Connect Unofficial**, restart Home Assistant, and continue
   with step 3 in the installation instructions above.

The intended setup creates and stores a private Home Assistant companion
without asking for a Weber account password. The documented clean-install path
has been validated end to end on the equipment below.

### Replacing the 2.1 add-on

The native 3.x integration is not an in-place add-on upgrade. In the 2.1
panel, use **Forget This Hub**, then stop and uninstall the add-on before
installing the native integration. It creates a new device, probe-temperature
entities appropriate for the controller, two connection-context entities, and
a grill-temperature entity when the appliance reports a built-in sensor; it
does not import the add-on's MQTT entities or settings. If
an old unavailable MQTT device remains, remove its retained discovery records
from the broker and delete that MQTT device from Home Assistant. The add-on and
its MQTT broker are not needed by the native integration.

## Everyday behavior

After cloud association succeeds, the default mode is **Phone + Home
Assistant**. Home Assistant keeps one Weber Cloud companion socket open and
requests fresh status on a 10-second cadence, leaving the hub's single
Bluetooth connection available to the Weber app. Recipes continue to be
started and managed in the Weber app while Home Assistant monitors the built-in
grill temperature and the controller's available probe slots.

Versions through 3.1.2 offered a local null-session telemetry mode. Because the
observed protocol does not provide a peer-authentication mechanism, that mode is
retired fail closed. Existing entries that selected it migrate to the
authenticated cloud companion when reloaded.

Probe entities retain stable slot IDs such as `probe_2_temperature`. Optional
nicknames keep the physical number visible—for example, **Brisket · Probe 2**—
without changing the entity's identity. Settings show Probe 1 and Probe 2,
plus any additional probe slots already registered for that hub. Previously
saved names are retained even when their fields are hidden. Weber Cloud is
used automatically; there is no connection-mode selector.

The device page starts with **Probe 1** and **Probe 2**, which are common to the
supported controllers. **Probe 3** and **Probe 4** are added only after the
controller reports those slot numbers, so a two-port model does not receive
unused entities. The built-in **Grill temperature** entity is likewise added
only when the controller reports that sensor. A diagnostic **Battery** entity
and **Charging** entity are added when a portable hub reports its power state.
Once created, each entity keeps its stable slot-based identity. A connected
probe shows its temperature; an empty slot—or a sleeping or powered-off hub
with no current reading—reads **Unknown** with the probe-off icon. That is the
normal idle state, not a sign that the integration or Home Assistant is
offline. Routine disconnects recover quietly without raising a Home Assistant
repair. Battery level, probe type, and probe state remain attributes on that
same entity instead of creating redundant entities.

Reading-status entities explain whether Home Assistant is waiting for its first
update, receiving updates, reconnecting, or has lost the connection. Each
supported probe also has a reading-status entity. **No probe reading reported**
means fresh telemetry contains no temperature for that slot; it does not claim
that the probe is unplugged. **Hub reports powered off** is used only when the
received device state explicitly says so. An unreachable hub is never assumed
to be sleeping.

During interruptions, sensor attributes include the last successful update
alongside the reading status, including on retained battery and Wi-Fi values.
This timestamp describes the last received appliance update, not a separate
measurement time for each field. The dedicated **Last successful update** entity
tracks live updates without adding timestamp-only history records to every sensor.

Two additional context entities are enabled by default. **Connection** reports
**Connected** or **Disconnected** and identifies **Weber Cloud** as the live
transport. **Last successful update** preserves the
time fresh hub data most recently arrived, including while the hub is sleeping
or powered off.

Additional entities are capability-driven and appear only after the appliance
reports their underlying telemetry. Depending on the Weber model, these can
include target grill temperature, cook mode and intensity, active cooking,
Wi-Fi and Weber Cloud status, Wi-Fi signal strength, fuel percentage or coarse
fuel level, wireless-probe battery/case/ambient readings, cook and timer
countdowns, and burner state. Session progress and burner details remain
attributes on their corresponding entity to avoid duplicating every protocol
field as a separate entity. Software and hardware versions are recorded on the
Home Assistant device and in diagnostics.

The integration is deliberately read-only. It exposes reported targets and
timer progress, but does not expose recipe text or instructions and cannot
start, stop, or change cooks, temperatures, timers, burners, or other appliance
controls.

## Requirements

- Home Assistant 2026.7.0 or newer.
- HACS for installation.
- A connectable Home Assistant Bluetooth adapter or active ESPHome Bluetooth
  proxy in range during setup.
- Home Assistant internet access and a hub that is already online in Weber
  Cloud for every initial installation and for live telemetry.

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

Version 3.0.3 is also community-verified on a **Weber Performer Deluxe Smart
57** (also sold as the **Performer Premium Smart**) running firmware
`2.9.0.8076` in **Phone + Home Assistant** mode. The reporter confirmed that
the new built-in **Grill temperature** entity works correctly alongside the
external probes. This is a successful compatibility report rather than a
controlled endurance test; see [issue #24](https://github.com/ProspectOre/weber-connect-unofficial/issues/24).
The same report confirmed that this model has two physical probe ports; version
3.0.4 therefore keeps Probe 3 and Probe 4 absent unless the controller actually
reports them.

Earlier 3.0 candidates also exercised a proxy-only null-session telemetry path.
That historical availability result is not a current security claim: the path
is now disabled because CRC and framing checks do not authenticate the sender.

The exact 3.1.0 candidate was subsequently installed on Home Assistant 2026.8.1
and exercised against the Weber Connect Hub `2.0.3_7398`. After a full Core
restart it exposed live battery and charging state, Wi-Fi signal and status,
Weber Cloud status, device state, and firmware metadata. Battery changed from
65% to 70% during the validation window while charging. A config-entry reload
preserved the entity identities, resumed cloud updates with zero failures, and
produced no Weber warning or error log entries.

The current greenfield transport implementation is held to at least 95%
combined statement/branch coverage. Import, config flow, transient
identity generation, entity contracts, protocol frames, persistent-session
cleanup during pairing, diagnostics redaction, and transport ownership are
covered. Live smoke and config-entry reload tests cover the persistent
WebSocket lifecycle.
The final persistent cloud test ran for more than 70 minutes with the Weber app
open and an active cook. See
[Production readiness](PRODUCTION_READINESS.md) for the measurements and
remaining unverified scenarios. The corresponding
[redacted machine-readable evidence](docs/validation/3.1.1-rc-physical.json)
contains no device identifiers. Multi-proxy failover is explicitly unverified.

That is a test matrix, not a claim that every Weber model, firmware, account
region, or proxy has been certified. Compatibility reports and pull requests
are welcome; see [Contributing](CONTRIBUTING.md) for the safe details to include.

## Troubleshooting

### No hub is discovered or no approval prompt appears

- Fully close the Weber app and disable Bluetooth on every phone or tablet that
  uses the hub. The hub accepts only one active Bluetooth owner.
- Wake the hub and keep it near a connectable Home Assistant Bluetooth adapter
  or an active ESPHome Bluetooth proxy with a free connection slot.
- Confirm the hub advertises on **Settings → Devices & services → Bluetooth**,
  then choose **Search again** in the setup flow.
- If the hub recently restarted, wait for it to finish booting before retrying;
  its advertisement can appear before its complete GATT service table is ready.

### Setup waits for Weber Cloud

Initial setup requires both Home Assistant internet access and a working
hub-to-Weber Cloud connection. With the phone's Bluetooth disabled, open the
Weber app and verify the hub still appears online through Wi-Fi. Restore the
hub's Wi-Fi connection or wait for a temporary Weber outage to clear, then use
the setup flow's cloud retry action.

### Probe entities are `Unknown`

`Unknown` is normal when a probe is unplugged or the hub is sleeping, powered
off, or temporarily unreachable. Wake the hub and inspect **Connection** and
**Last successful update** on its device page. The integration retries quietly
and republishes temperatures when fresh data arrives.

### Keep Activity focused on changes

The **Last successful update** sensor stays precise on every successful poll.
To keep those timestamp changes out of Activity while retaining the live sensor
and its History, merge this into your existing `configuration.yaml` logbook
filter (replace the example entity ID with your hub's actual entity ID):

```yaml
logbook:
  exclude:
    entities:
      - sensor.weber_connect_hub_last_successful_update
```

Keep existing exclusions and use only one `logbook:` section. Check the Home
Assistant configuration, then restart to apply the filter. Connection and probe
reading-status transitions remain visible. This filters Activity without
deleting recorded history or changing the polling cadence.

Established sockets that drop between polls get one immediate reconnect attempt.
Only a fresh cooking/probe status completes recovery; appliance-only traffic
does not refresh temperature timestamps. Persistent failures still enter the
normal reconnect backoff and stale-reading states. Diagnostics include socket
connection and fast-recovery counts and the last failure's exception type to
help distinguish short relay disconnects from timeouts.

### Weber rejects the private connection

Open the credential repair and continue, then open the integration's
reauthentication prompt in **Settings → Devices & services**. Follow the setup
steps and approve the replacement companion on the same physical hub. Home
Assistant keeps the existing entry, entity IDs, probe names, and automation
references. It replaces the stored connection only after physical pairing and
the cloud association check succeed. Cancelling or failing recovery leaves the
existing configuration intact. Physical approval is still required.

### Collecting diagnostics

Open **Settings → Devices & services → Weber Connect Unofficial**, select the
three-dot menu for the config entry, and download diagnostics. Identifiers and
stored credentials are redacted. Attach that file, the Home Assistant version,
hub model and firmware, and relevant logs to a
[GitHub issue](https://github.com/ProspectOre/weber-connect-unofficial/issues).

## Removing the integration

1. Open **Settings → Devices & services → Weber Connect Unofficial**.
2. Select the three-dot menu for the config entry and choose **Delete**. This
   stops the active transport and deletes the locally stored companion
   credential, device, and entities.
3. To remove the integration files as well, open HACS, select **Weber Connect
   Unofficial**, choose **Remove**, and restart Home Assistant.

Weber provides no supported companion-revocation endpoint. An unused
server-side companion record can therefore remain after removal, but it has no
Weber account password and the local credential is deleted with the config
entry.

## Privacy

The integration generates a random companion ID, cloud device password, and
transient pairing value. Only the approved companion identity and cloud
credential are stored in the config entry; the pairing value is discarded.
Diagnostics redact stored credentials and all hub/companion identifiers. The
integration never asks for the user's Weber account password and does not copy
secrets from the official app.

Weber Cloud is private and undocumented. The integration sends Home Assistant's
generated identity and read-only current-status requests to Weber. It does not
accept local null-session status as trusted telemetry.

Registering the private companion happens before physical approval. If setup is
abandoned after registration, Weber may retain an unused server-side companion
record; it contains no Weber account password, and Weber provides no supported
revocation endpoint. Removing the Home Assistant entry always deletes the local
credential.

## Project documents

- [Architecture](ARCHITECTURE.md)
- [ADR 0001: superseded proxy relay](docs/adr/0001-home-assistant-bluetooth-proxy-transport.md)
- [ADR 0002: native transport lifecycle](docs/adr/0002-native-transport-lifecycle.md)
- [ADR 0003: authenticated telemetry boundary](docs/adr/0003-authenticated-telemetry-boundary.md)
- [Production readiness](PRODUCTION_READINESS.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [GitHub wiki](https://github.com/ProspectOre/weber-connect-unofficial/wiki)

# Weber Connect for Home Assistant (Unofficial)

## What It Does

The add-on reads a Weber Connect Hub over Bluetooth Low Energy and publishes
four stable probe slots to Home Assistant through MQTT discovery. Everything is
managed from its built-in Home Assistant panel.

The control center is designed to keep connection source, all four probe slots,
and Home Assistant publishing health visible on one screen.

BLE is always preferred. Recommended onboarding also configures Weber Cloud so
the official Weber app can own the hub's single BLE connection while Home
Assistant continues reading probe telemetry. The bridge creates its own Weber
companion identity; users do not need to reveal a Weber account password or
extract a secret from a phone. A **Local only** setup remains available.

The integration is read-only. It does not start recipes, change targets or
timers, configure Wi-Fi, or control a grill.

## Requirements

- Home Assistant OS, Supervised, or another installation with add-on support.
- A working Bluetooth adapter available to the Home Assistant host.
- The MQTT integration and a broker. The Mosquitto broker add-on is the easiest
  option and is discovered automatically.
- Internet access for the recommended phone-coexistence setup; **Local only**
  works without it.

## Verified Compatibility

The 2.0 physical test matrix is intentionally specific:

| Component | Verified setup |
| --- | --- |
| Hub | Weber Connect Hub |
| Home Assistant host | Home Assistant Yellow |
| Phone client | Official Weber app on Android |
| Local transport | Bluetooth Low Energy through host BlueZ/D-Bus |
| Shared-use path | Official app owns BLE while Home Assistant receives Weber Cloud probe snapshots |
| Cook scenario | Probe telemetry from a recipe started in the official app |

The release also passes 302 deterministic tests, strict type checking, linting,
release validation, and a 95% branch-coverage gate. Automated tests exercise
pairing, cloud registration and authentication, appliance association,
pagination, handoff, MQTT discovery, persistence, malformed input, stale data,
and the panel contracts.

This matrix describes what maintainers tested; it is not a universal
certification. Other hub models, firmware versions, Home Assistant hosts,
Bluetooth adapters, app versions, accounts, and regions may behave differently,
especially because Weber's cloud API is private and undocumented. Successful
and unsuccessful compatibility reports are welcome. See
[CONTRIBUTING.md](../CONTRIBUTING.md) for the safe details to include and how to
submit a fix.

## Recommended First Run

1. Add this repository to the Home Assistant app/add-on store.
2. Install and start **Weber Connect for Home Assistant (Unofficial)**.
3. Open its Web UI.
4. Power on the Weber hub, keep it near the Home Assistant Bluetooth adapter,
   and select **Set Up My Hub**.
5. When the hub beeps, press its physical button to confirm pairing.
6. Keep the hub powered and online while Weber publishes the private companion
   association. This can take up to five minutes.
7. Wait for **Connected**. MQTT discovery creates the probe entities
   automatically, and phone coexistence is ready.

This flow registers the bridge companion before BLE pairing and uses the same
identity for local and cloud access. It does not require identifiers, YAML, a
Weber login, or a second pairing pass. Select **Local only** to keep the setup
BLE/MQTT-only.

## Panel States

| State | Meaning |
| --- | --- |
| Connected | A current BLE or cloud read succeeded. |
| Monitoring through cloud | The official app can own BLE while new cloud snapshots reach Home Assistant. |
| Free for the Weber app | Bluetooth has been released for a timed or manual-reconnect phone session. |
| Hub unreachable | The hub is off, asleep, out of range, or busy; retry is automatic. |
| Pairing | The bridge is waiting for the hub exchange and, when required, physical confirmation. |

The panel labels retained values **Last known reading** after a failed live
read. MQTT receives explicit disconnected availability rather than presenting
stale values as current automation data.

The hub has four fixed probe slots. Empty slots remain present with `No probe`
state and a `null` temperature so Home Assistant entity IDs do not churn.
Each slot can also have an optional nickname. The panel and MQTT discovery keep
the physical identity in the displayed name—for example,
`Brisket · Probe 1 Temperature`—so a nickname never hides the probe number.

## Pair With Weber Cloud

Phone coexistence uses Weber's private, undocumented `walker-cloud` service and
may stop working if Weber changes that service. It is the recommended setup
when Home Assistant and the official Weber app need access to the same hub.
Local-only pairing remains available.

### Normal setup

On a fresh installation, select **Set Up My Hub** on the first screen and press
the hub button when prompted. The bridge completes local and cloud companion
pairing together. On an existing local-only installation:

1. Open the panel's **Settings**.
2. Under **Phone + Home Assistant**, select **Set up phone coexistence**.
3. If the hub prompts, press its physical button.
4. Keep the hub powered, online, and near Home Assistant. Weber may take up to
   five minutes to publish the new association.
5. When the panel reports that cloud access is ready, select **Test**.

The generated companion belongs to this add-on installation and does not use a
personal Weber login. Setup performs these companion-level operations:

1. Generate a fresh companion ID, device password, and companion keys.
2. Register that identity with Weber Cloud before BLE pairing.
3. Present the same ID during BLE pairing and complete the paired companion
   handshake.
4. Wait for Weber Cloud to associate that companion with the hub.
5. Verify that the identity can read the specific appliance, not merely obtain
   an authentication token.

The generated password is never displayed or returned by the status API. The
identity is stored in `/data/weber-connect-bridge/cloud_credentials.json` with
mode `0600`.

### Advanced recovery fields

**Use existing companion credentials** is for advanced recovery and research.
It accepts an existing companion/App Identifier and its matching device
password. This device password is separate from a Weber account password and is
normally not visible in the official app.

The provisioning verification-code field is retained for legacy/manual
association flows. It is not part of the normal bridge-owned companion setup.
Do not reset or reconfigure hub Wi-Fi merely to obtain a code unless a specific
legacy device requires that recovery path.

**Remove Credentials** deletes the local identity. It cannot remove a companion
record already held by Weber because this private flow has no supported remote
revocation endpoint.

## Use The Official Weber App At The Same Time

The hub accepts only one active BLE client and normally stops advertising while
connected. Without cloud support, Home Assistant and the official app therefore
take turns.

1. Configure and test cloud support.
2. Select **Use with Phone** in the panel.
3. Choose a phone-session duration and select **Release Bluetooth**.
4. Open the Weber app and connect to the hub.

The app then owns Bluetooth while Home Assistant polls Weber Cloud. When cloud
coexistence is ready, **Manual reconnect** is preselected because telemetry can
continue without an arbitrary BLE deadline. In this mode, automatic reconnect
is off until **Reconnect now** is selected. If cloud is unavailable, the panel
preselects the saved timed fallback (15 minutes on a fresh install). The saved
fallback is not rewritten by the adaptive recommendation.

Handoff state survives add-on restarts. Stopping the add-on also releases its
BLE connection cleanly.

## Recipes And Cooking Data

Starting a recipe in the official app works during cloud handoff. The hub
continues uploading the cook session, and Home Assistant receives the new probe
snapshots while the phone remains connected.

Current Home Assistant entities expose:

- Probe temperature
- Probe connection/state
- Probe battery when available
- Bridge/cloud connectivity and source metadata

The bridge does not currently expose the recipe name, recipe instructions,
doneness selection, target changes, or timers as controllable Home Assistant
entities. It never sends recipe or grill-control commands.

Weber cloud temperatures are encoded in tenths of a degree Celsius. The bridge
normalizes them and publishes correct Fahrenheit and Celsius values. Existing
bridge-generated cloud identities created with the older Fahrenheit assumption
are migrated automatically.

## Configuration

Most settings live in the panel. Only two Supervisor options are exposed:

| Option | Default | Description |
| --- | ---: | --- |
| `log_level` | `info` | Add-on log verbosity. |
| `mqtt` | empty | External MQTT broker settings; leave blank for automatic Mosquitto service discovery. |

Panel settings include read interval, phone handoff duration, probe nicknames,
cloud pairing, cloud test/disable/removal, and **Forget This Hub**. New installs
use the **Live · 10 sec** local read interval. Existing installations retain
their saved interval until it is changed in the panel.

## Home Assistant Entities

The add-on publishes one **Weber Connect Hub** MQTT device with these entities
for each of four probe slots:

| Entity Type | Example |
| --- | --- |
| Temperature sensor | `Probe 1 Temperature` |
| State sensor | `Probe 1 State` |
| Battery sensor | `Probe 1 Battery` |

With the optional nickname `Brisket`, these become
`Brisket · Probe 1 Temperature`, `Brisket · Probe 1 State`, and
`Brisket · Probe 1 Battery`. MQTT unique IDs stay unchanged, so renaming does
not create a new entity.

Default state topic:

```text
weber_connect/{device_id}/state
```

Example discovery topics:

```text
homeassistant/sensor/{device_id}_probe_1_temperature/config
homeassistant/sensor/{device_id}_probe_1_state/config
homeassistant/sensor/{device_id}_probe_1_battery/config
```

## Troubleshooting

### Set Up My Hub finds nothing

1. Power on and wake the hub.
2. Move it closer to Home Assistant's Bluetooth adapter.
3. Fully close the Weber app; a connected hub does not advertise.
4. Select **Scan Again**.

### Pairing fails

1. Press the physical hub button when it beeps.
2. Keep the hub awake and nearby; BLE confirmation can take up to 90 seconds.
3. Ensure no phone or tablet is connected.
4. Power-cycle the hub and retry after a decline or timeout.

### No Home Assistant entities appear

1. Check the panel footer for MQTT publishing status.
2. Confirm the MQTT integration and broker are running.
3. Reload MQTT entities or restart Home Assistant if discovery was only just
   enabled.

### Cloud pairing appears stuck

1. Allow the full five-minute association window.
2. Keep the hub powered and connected to its already-configured Wi-Fi.
3. Confirm the physical pairing prompt if the hub displays one.
4. Select **Test** after setup completes.
5. If authentication works but appliance access is denied, remove the failed
   identity and repeat **Pair with Weber Cloud** while the hub is online.

### Cloud is ready but no current reading appears

1. Confirm the official app itself displays a current probe temperature.
2. Start or resume a cook/recipe so the hub publishes current snapshots.
3. Allow one or two configured poll intervals.
4. Cloud is reported idle when no active snapshot arrives beyond the stale-data
   grace window.

### The phone cannot connect

1. Select **Use with Phone**, then confirm **Release Hub**.
2. Wait for **Free for the Weber app** before opening the official app.
3. If needed, force-close and reopen the Weber app after the release.

## Security And Privacy

- The panel is reachable through Home Assistant ingress; no host port is
  exposed.
- Pairing keys, cloud credentials, handoff state, runtime status, and MQTT
  credentials are stored privately under `/data/weber-connect-bridge`.
- Passwords and bearer tokens are excluded from logs and public status data.
- Cloud support does not capture the official app, intercept TLS, or require a
  personal Weber login.
- Enabling cloud sends authentication and cook-history requests to Weber's
  service. Leaving it disabled keeps the bridge BLE/MQTT-only.
- Do not attach private captures, pairing exports, phone app data, or runtime
  credential files to public issues.

## Privileges

| Privilege | Requested | Reason |
| --- | --- | --- |
| `host_dbus` | Yes | BlueZ access over the host D-Bus system bus. |
| `NET_ADMIN` | No | The add-on does not manage network interfaces. |
| `NET_RAW` | No | The add-on does not use raw sockets. |
| `udev` | No | BlueZ mediates hardware access. |
| AppArmor profile | Yes | Restricts runtime file, D-Bus, network, and process access. |

## Support Boundary

This project is unofficial and is not affiliated with Weber. BLE firmware and
the private cloud API may vary by hub model or change without notice. Include
the add-on version, Home Assistant version, hub model, and redacted logs when
reporting a problem.

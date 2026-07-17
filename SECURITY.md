# Security Policy

## Supported Versions

The latest released version receives security fixes. Earlier releases are not
supported.

## Reporting

Use GitHub's private **Report a vulnerability** form. If private advisories are
unavailable, open an issue requesting maintainer contact without technical
details and continue privately.

Include only the add-on version, Home Assistant version, hardware platform,
redacted logs, and high-level reproduction steps. Never publish pairing
summaries, key files, cloud passwords or tokens, MQTT passwords, Android app
data, or full BLE/network captures.

## Trust Boundaries

- The web panel is served through Home Assistant ingress. Mutating routes also
  verify Supervisor ingress network provenance.
- BlueZ access is mediated through the host D-Bus interface under the bundled
  AppArmor profile. The add-on does not request `NET_ADMIN`, `NET_RAW`, or raw
  device access.
- Local pairing keys, the bridge-owned cloud identity, tokens, handoff state,
  and MQTT credentials are private runtime data. JSON state uses owner-only
  permissions and atomic replacement.
- Status responses and logs do not expose cloud passwords, bearer tokens, or
  full companion identifiers.

## Cloud Security Model

Cloud support is opt-in. The bridge generates its own random companion ID,
device password, and key material; it does not ask users for their Weber account
password or copy secrets from the official app. Pairing still requires physical
access to the hub when it requests confirmation.

The app-global Weber client values in the source identify the Weber application,
not an individual user. The per-install companion password and bearer tokens are
the sensitive credentials.

Weber's cloud API is private and undocumented. Enabling it sends companion
authentication and read-only cook-history requests to Weber. The bridge never
uses the cloud path to start recipes, change targets, configure Wi-Fi, or control
a grill.

**Remove Credentials** deletes the local cloud identity but cannot remove a
server-side companion record because Weber exposes no supported revocation API
for this flow.

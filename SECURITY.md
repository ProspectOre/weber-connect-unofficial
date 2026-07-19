# Security policy

## Supported versions

The latest released version receives security fixes.

## Reporting

Use GitHub's private **Report a vulnerability** form. If private advisories are
unavailable, open an issue requesting maintainer contact without technical
details and continue privately.

Never publish config-entry exports, companion keys, cloud passwords or tokens,
MAC addresses, appliance IDs, Weber account details, packet captures, or
unredacted diagnostics.

## Trust boundaries

- Home Assistant Core owns config-entry storage, Bluetooth adapters, ESPHome
  proxy credentials, connection-slot allocation, and entity authorization.
- The integration resolves hubs only through Home Assistant's documented
  Bluetooth API and uses `bleak-retry-connector` for bounded connection retry.
- The integration never reads Home Assistant `.storage`, contacts ESPHome
  proxies directly, or exposes a general-purpose GATT transport.
- Every local connection is released in `finally`, including cancellation and
  protocol failure paths.
- Diagnostics redact the hub address, appliance and companion IDs, cloud
  password, companion keys, and appliance public key.
- Cloud REST requests are restricted to HTTPS and WebSocket requests to WSS.

## Cloud security model

The integration generates its own random companion identity. It does not ask
for a Weber email/password and does not copy a phone secret. Pairing still
requires physical access and confirmation on the hub.

The app-global Weber client values in source identify Weber's application, not
an individual user. The generated companion password, key material, and bearer
token are sensitive per-install credentials.

Weber's cloud API is private and undocumented. The default **Phone + Home
Assistant** mode sends
authentication, association, cook-history, live-session, and program-detail
requests to Weber. Remote commands are separately opt-in and limited to
confirming or stopping an active cook and resetting an existing timer.

The integration does not configure Wi-Fi, install or start recipes, change
targets, ignite appliances, or change grill mode. Weber exposes no supported
companion-revocation API; deleting the Home Assistant config entry removes the
local credential but may not remove Weber's server-side companion record.

## Supply chain

Releases must pass Ruff, strict mypy, Bandit, CodeQL, dependency auditing,
Home Assistant Hassfest, HACS validation, native config-flow tests, and protocol
tests. GitHub Actions use immutable commit SHAs.

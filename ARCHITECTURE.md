# Architecture

3.0 runs entirely inside Home Assistant Core as a custom integration.

```text
Home Assistant config entry
          │
          ▼
   update coordinator
      │         │
      │         └── Weber Cloud connection ── phone + Home Assistant / live cook
      │
      └── Home Assistant Bluetooth manager
                    │
                    ├── local adapter
                    └── active ESPHome proxy
                              │
                              ▼
                        Weber Connect Hub

coordinator ──► four permanent native probe temperature sensors
```

There is no MQTT broker, separate web app, copied proxy secret, or
general-purpose remote GATT service.

## Setup lifecycle

1. Home Assistant Bluetooth discovery matches Weber manufacturer identifiers or
   the Weber local name through any connectable scanner.
2. The config flow generates an independent 16-byte companion ID, device
   password, and opaque companion key material.
3. Home Assistant resolves the hub through `async_ble_device_from_address`.
4. `bleak-retry-connector` establishes GATT through the best local adapter or
   active proxy and re-resolves that path on every retry.
5. The integration claims the Weber session characteristic, negotiates the
   message version, submits the companion identity, and requires confirmation
   on the physical hub.
6. The integration registers the approved identity with Weber Cloud and waits
   for Weber to associate it with the appliance.
7. Home Assistant stores the private identity in the config entry and creates
   stable native entities.
8. Normal updates use Weber Cloud by default so the app retains Bluetooth.

## Update policy

- Default: poll the companion cloud session every 10 seconds. This leaves the
  hub's single Bluetooth connection available to the official app.
- Home Assistant only: read status through Home Assistant Bluetooth every 10
  seconds.
- Optional fallback: use a local/proxy GATT read if cloud access fails. This is
  disabled by default because it can take Bluetooth from the phone.
- Every GATT operation disconnects in `finally`; no adapter or proxy connection
  is held between updates.

The coordinator normalizes both transports into one stable state shape. Entity
unique IDs use the config entry's hub address plus a semantic slot key, so a
proxy change or user-visible rename does not create new entities.

## Security boundary

Home Assistant owns Bluetooth adapters, ESPHome credentials, proxy allocation,
config-entry storage, entity permissions, and diagnostics download. The
integration receives only a resolved `BLEDevice` and never reads `.storage` or
contacts an ESPHome proxy directly.

Weber Cloud credentials are generated per hub. Diagnostics redact the hub
address, appliance and companion IDs, cloud password, and companion keys.
Cloud and GATT operations have bounded timeouts. The integration is read-only.

## Private protocols

`saber_frames.py` implements Weber's observed null-session transport, pairing,
and cook-status TLV decoding. `weber_cloud.py` and `weber_cloud_socket.py`
implement the minimal read-only companion REST/WebSocket surface for
association, telemetry, and program details. These interfaces are private and
can change without notice.

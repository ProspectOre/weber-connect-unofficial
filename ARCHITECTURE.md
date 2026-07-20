# Architecture

3.0 runs entirely inside Home Assistant Core as a custom integration.

```text
Home Assistant config entry
          │
          ▼
  transport coordinator
          │
          ├── Phone + Home Assistant: one companion WebSocket
          │
          └── Home Assistant only: one persistent GATT session
                                      │
                            Home Assistant Bluetooth manager
                                      │
                             local adapter or active proxy

coordinator ──► four permanent native probe temperature sensors
```

There is no MQTT broker, separate web app, copied proxy secret, or
general-purpose remote GATT service.

## Setup lifecycle

1. Home Assistant Bluetooth discovery matches Weber manufacturer identifiers or
   the Weber local name through any connectable scanner.
2. The config flow generates an independent 16-byte companion ID, cloud device
   password, and transient pairing key material.
3. Home Assistant resolves the hub through `async_ble_device_from_address`.
4. `bleak-retry-connector` establishes GATT through the best local adapter or
   active proxy and re-resolves that path on every retry.
5. The integration claims the Weber session characteristic, negotiates the
   message version, submits the companion identity, and requires confirmation
   on the physical hub.
6. The integration registers the approved identity with Weber Cloud and waits
   for Weber to associate it with the appliance.
7. Home Assistant stores the companion ID, cloud device password, appliance ID,
   hub address, and negotiated message version. Transient pairing keys are
   discarded.
8. Normal updates use Weber Cloud by default so the app retains Bluetooth.

## Update policy

- Default: retain one authenticated companion WebSocket and request fresh
  status on a start-to-start 10-second cadence. This leaves the hub's Bluetooth
  connection available to the official app.
- Home Assistant only: retain one subscribed GATT connection through Home
  Assistant's selected adapter or active proxy and request status on the same
  cadence.
- Reconnect only after a real link loss. An ESPHome proxy slot remains allocated
  while Home Assistant-only mode owns the live connection and is released on
  link failure, config-entry reload, or shutdown.
- Never fail over automatically between cloud and Bluetooth. Changing mode
  reloads the entry and closes the old transport first.

The coordinator normalizes both transports into one stable state shape. Entity
unique IDs use the config entry's hub address plus a semantic slot key, so a
proxy change or user-visible rename does not create new entities.

## Security boundary

Home Assistant owns Bluetooth adapters, ESPHome credentials, proxy allocation,
config-entry storage, entity permissions, and diagnostics download. The
integration receives only a resolved `BLEDevice` and never reads `.storage` or
contacts an ESPHome proxy directly.

Weber Cloud credentials are generated per hub. Diagnostics redact the hub
address, appliance and companion IDs, cloud password, and legacy secret-key
fields. Raw protocol frames and recipe metadata are excluded. Cloud and GATT
operations have bounded timeouts. The integration is read-only.

## Private protocols

`saber_frames.py` implements Weber's observed null-session transport, pairing,
and cook-status TLV decoding. `weber_cloud.py` and `weber_cloud_socket.py`
implement the minimal read-only companion REST/WebSocket surface for
association and probe telemetry. Cook-history, recipe, instruction, timer, and
control APIs are outside the 3.0 runtime. These interfaces are private and can
change without notice.

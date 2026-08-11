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

coordinator ──► two baseline native probe temperature sensors
            ├─► one connection binary sensor
            ├─► one last-successful-update timestamp sensor
            └─► capability-driven sensors and binary sensors when reported
                 (grill, target, battery, charging, cooking, connectivity,
                  fuel, wireless probes, sessions, timers, and burners)
```

There is no MQTT broker, separate web app, copied proxy secret, or
general-purpose remote GATT service.

## Setup lifecycle

1. Home Assistant Bluetooth discovery matches Weber manufacturer identifiers or
   the Weber local name through any connectable scanner.
2. The config flow generates an independent 16-byte companion ID, cloud device
   password, and transient 64-byte pairing value required by the hub protocol.
3. Home Assistant registers that private companion with Weber Cloud before it
   presents the companion ID to the hub. This ordering lets the hub publish the
   cloud association during the one-time Bluetooth approval session.
4. Home Assistant resolves the hub through `async_ble_device_from_address`.
5. `bleak-retry-connector` establishes GATT through the best local adapter or
   active proxy and re-resolves that path on every retry.
6. The integration claims the Weber session characteristic, negotiates the
   message version, submits the companion identity, and requires confirmation
   on the physical hub.
7. The integration waits for Weber's association list to contain the exact hub;
   it never treats local approval alone as proof of cloud access.
8. Home Assistant stores the companion ID, cloud device password, appliance ID,
   hub address, and negotiated message version. The transient pairing value is
   discarded.
9. Normal updates use Weber Cloud by default so the app retains Bluetooth.

Registration necessarily precedes physical approval. If the flow is abandoned
after step 3, Weber may retain an unused companion record because its private
API exposes no supported revocation operation. No config entry is created and
Home Assistant retains no credential after the flow is discarded.

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

The coordinator normalizes both transports into one stable state shape. Probe
1 and Probe 2 form the baseline entity set. Probe 3, Probe 4, and other
appliance-specific entities are added only when decoded status reports the
corresponding capability. This includes hub power, appliance connectivity,
fuel, cook status, wireless-probe details, countdowns, and burner state. Once
created, probe entities retain their physical slot identity while current
values may become `Unknown`. An always-visible connection binary sensor reports
the current transport state and method, while a timestamp sensor preserves the
last successful read. Entity unique IDs use the config entry's hub address plus
a semantic key, so a proxy change or user-visible rename does not create new
entities. Reported software and hardware versions update Home Assistant's
device registry metadata.

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
appliance-status, and cook-status TLV decoding. `weber_cloud.py` and
`weber_cloud_socket.py` implement the minimal read-only companion
REST/WebSocket surface for association and live appliance telemetry.
Cook-history, recipe, instruction, and control APIs are outside the 3.0
runtime. These interfaces are private and can change without notice.

The local decoder accepts only a complete transport frame whose declared
length, envelope CRC, and terminal marker all verify. The cloud decoder accepts
status only when the routing source is the configured appliance and the target
is the generated companion. Pairing-response public-key bytes and the envelope's
reserved verification byte are decoded only to validate the observed wire
shape; they are never persisted or used as credentials. The obsolete manual
cloud-association endpoint and unused generated private-key field are absent
from the runtime.

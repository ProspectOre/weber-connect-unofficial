# ADR 0001: Home Assistant Bluetooth Proxy Transport

- Status: Superseded by the 3.0 native integration
- Date: 2026-07-18

## Historical context

The retired 2.1 add-on used Bleak through the Home Assistant host's BlueZ
D-Bus. That reached local Bluetooth adapters, but not ESPHome Bluetooth
proxies.
Home Assistant Core owns the Bluetooth manager that aggregates local adapters
and remote proxies, chooses the best connection path, allocates proxy slots,
and fails over between scanners.

ESPHome proxies support active GATT connections when `bluetooth_proxy.active`
is enabled. Home Assistant exposes proxy advertisements over its authenticated
WebSocket API, but it does not expose a generic public WebSocket API for GATT
connect, read, write, and notification operations. A container permission or
BlueZ configuration change therefore could not add proxy support to that
add-on.

Relevant upstream interfaces:

- [Home Assistant Bluetooth integration guidance](https://developers.home-assistant.io/docs/bluetooth/)
- [Home Assistant Bluetooth proxy capabilities](https://www.home-assistant.io/integrations/bluetooth/#remote-adapters-bluetooth-proxies)
- [ESPHome active connection behavior](https://esphome.io/components/bluetooth_proxy/#how-active-connections-work)

## Original decision

Proxy support was initially planned around an optional Home Assistant
companion integration. That integration would run inside Home Assistant Core
and use only documented
Bluetooth APIs:

- depend on `bluetooth_adapters` so remote scanners are ready before setup;
- resolve connectable devices with `async_ble_device_from_address`;
- connect with `bleak-retry-connector`, allowing Home Assistant to select and
  fail over between local adapters and active proxies;
- expose a versioned, Weber-specific WebSocket transport to the add-on;
- bind every BLE session to the authenticated WebSocket connection and close it
  when that connection ends.

The add-on would connect to Home Assistant's WebSocket API through Supervisor
using its injected `SUPERVISOR_TOKEN`. No user token, ESPHome encryption key,
Home Assistant storage file, or proxy credential will be copied into the
add-on.

The proposed transport protocol would allow only the Weber service and characteristic
UUIDs already implemented by this project. Requests and notifications will
have bounded sizes, operation deadlines, one active session per hub, and
explicit disconnect semantics. It will not be a general-purpose Bluetooth
remote-execution API.

## Rejected alternatives

- **Expose a proxy through BlueZ:** ESPHome proxies terminate in Home Assistant
  Core and do not appear as BlueZ adapters on the host.
- **Use advertisement WebSockets only:** Weber pairing and status reads require
  active GATT writes, reads, and notifications.
- **Connect directly from the add-on to ESPHome:** this would duplicate Home
  Assistant's proxy client, require copying proxy credentials, compete for
  connection slots, and bypass HA's adapter selection and failover.
- **Read Home Assistant `.storage`:** private implementation details and secrets
  are not a supported API and must not cross the add-on boundary.
- **Install a custom integration from the add-on:** mutating the user's Home
  Assistant configuration is outside the add-on's trust boundary.

## 3.0 outcome

The project moved the complete runtime into a native Home Assistant integration
instead of retaining an add-on relay. Home Assistant now owns discovery,
adapter/proxy selection, retry failover, config-entry storage, and native
entities. No add-on-to-Core WebSocket GATT transport is needed.

The physical acceptance criteria remain valid and are now maintained in
`PRODUCTION_READINESS.md`.

## Archived acceptance criteria

The relay design would have required all of the following to pass on real
hardware with the host Bluetooth adapter disabled:

1. Discover a Weber hub through an active ESPHome proxy.
2. Complete physical-confirmation pairing through that proxy.
3. Receive probe notifications and publish the add-on's retained MQTT state.
4. Sustain the configured 10-second cadence for at least one hour.
5. Recover after proxy restart, Home Assistant restart, and add-on restart.
6. Fail over between two active proxies without changing entity unique IDs.
7. Release the GATT connection promptly for the official Weber app.
8. Preserve the existing direct-BlueZ and cloud-coexistence paths.

These criteria describe the abandoned relay architecture and are not the 3.0
release matrix. The native integration's current, physically exercised matrix
is maintained in [`PRODUCTION_READINESS.md`](../../PRODUCTION_READINESS.md).

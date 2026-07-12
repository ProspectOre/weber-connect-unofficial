# Weber Connect BLE Bridge

## What It Does

This add-on reads Weber Connect Hub probe status locally over Bluetooth Low
Energy and publishes the result to Home Assistant through MQTT discovery.
Everything is managed from the add-on's built-in web panel.

It is intentionally read-only. It does not start cooks, change setpoints,
modify timers, configure Wi-Fi, or use Weber cloud credentials.

## Requirements

- Home Assistant with add-on support.
- A working Bluetooth adapter on the Home Assistant host.
- MQTT broker access. The Mosquitto broker add-on is the easiest path.
- MQTT integration enabled in Home Assistant.

## Setup

1. Install the add-on and start it.
2. Click **Open Web UI**.
3. Power on the Weber hub, keep it near the Home Assistant Bluetooth adapter,
   and tap **Find My Hub**.
4. When the hub beeps, **press the button on the hub** to confirm pairing —
   the same confirmation the Weber phone app asks for.
5. The panel finishes pairing and starts publishing probe sensors
   automatically.

That's the whole setup. There is no required configuration.

## The Panel

The web panel always shows the current connection state and what to do next:

| State | Meaning |
| --- | --- |
| Connected | Telemetry is flowing; probe cards show live readings. |
| Free for the Weber app | The hub is released for your phone; a countdown shows when the bridge reconnects. |
| Hub unreachable | The hub is off, asleep, or out of range. The bridge retries automatically. |

When a live read fails, the panel labels retained values as **Last known
reading**. MQTT receives an explicit disconnected state with empty probe
values, so stale telemetry is never presented as current automation data.

From the panel you can also:

- **Use with Phone** — releases the hub so the Weber app can find and use it.
  The bridge reconnects automatically when the handoff window ends, or
  immediately when you tap **Reconnect Now**.
- **Settings** (gear icon) — update interval, phone handoff duration, and
  **Forget This Hub**.

## Using The Weber Phone App

The Weber hub accepts only one active BLE connection at a time and does not
advertise while connected, so the phone app cannot find the hub while the
add-on holds the connection.

Tap **Use with Phone** in the panel. The add-on disconnects, clears any stale
Bluetooth connection, and waits. Open the Weber app on your phone; it will
find the hub normally. When the handoff window ends (15 minutes by default,
adjustable in Settings), the bridge reconnects on its own — nothing to
remember, nothing to reset.

The handoff deadline is stored durably. Restarting the add-on does not take
the hub back from the phone early; an indefinite handoff remains paused until
you select **Reconnect Now**.

Stopping the add-on also releases the hub cleanly.

## Configuration Options

Only two options exist; most installs never need to touch them.

| Option | Default | Description |
| --- | ---: | --- |
| `log_level` | `info` | Add-on log verbosity. |
| `mqtt` | empty | External MQTT broker settings. Leave blank to use the Mosquitto add-on service automatically. |

If the Mosquitto broker add-on is installed, MQTT requires no configuration at
all. The panel footer shows whether publishing to Home Assistant is working.

## Home Assistant Entities

The add-on publishes a single **Weber Connect Hub** MQTT device with, per
probe slot:

| Entity Type | Example Name |
| --- | --- |
| Temperature sensor | `Probe 1 Temperature` |
| State sensor | `Probe 1 State` |
| Battery sensor | `Probe 1 Battery` |

## Troubleshooting

If **Find My Hub** finds nothing:

1. Make sure the hub is powered on and awake (press a button on it).
2. Move the hub closer to the Home Assistant Bluetooth adapter.
3. Make sure the Weber phone app is fully closed — if the phone is connected,
   the hub does not advertise.
4. Tap **Scan Again**.

If pairing fails:

1. Press the button on the hub when it beeps — pairing is not confirmed
   until the button is pressed.
2. Wake the hub and keep it close, then try again. Pairing can take up to a
   minute and a half.
3. If the hub declines pairing, power-cycle the hub and retry.
4. Make sure no phone or tablet with the Weber app is connected to the hub;
   it only serves one Bluetooth connection at a time.

If no entities appear in Home Assistant:

1. Check the panel footer — it shows MQTT publishing status and errors.
2. Confirm the MQTT integration is enabled in Home Assistant.
3. Restart Home Assistant or reload MQTT entities if discovery was just
   enabled.

If the panel shows **Hub unreachable**:

1. Confirm the hub is powered and in range.
2. The bridge retries automatically at the configured interval.
3. If your phone's Weber app is connected to the hub, the bridge cannot reach
   it — close the app or wait for it to disconnect.

If probe values are unavailable:

1. Make sure probes are inserted and visible on the hub.
2. Wait one or two update cycles after inserting a probe.

## MQTT Topics

Default state topic:

```text
weber_connect/{device_id}/state
```

Default discovery topics:

```text
homeassistant/sensor/{device_id}_probe_1_temperature/config
homeassistant/sensor/{device_id}_probe_1_state/config
homeassistant/sensor/{device_id}_probe_1_battery/config
```

## Security Notes

- The panel is reachable only through Home Assistant ingress; no extra port is
  exposed.
- The add-on stores MQTT credentials and pairing material in
  `/data/weber-connect-bridge` with mode `0600`.
- The add-on logs the BLE address and MQTT host, but not MQTT passwords.
- Do not attach private pairing exports or Android app data to public issues.
- This project is unofficial and is not affiliated with Weber.

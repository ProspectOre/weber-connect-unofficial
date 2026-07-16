# Weber Connect BLE Bridge

Read-only local BLE bridge for Weber Connect Hub probe telemetry, managed from
a built-in web panel.

Open the add-on's **Web UI** to set up and manage everything:

- **Find My Hub** discovers and pairs your hub in one tap.
- Live connectivity status and probe temperatures, states, and battery levels.
- **Use with Phone** releases the hub for the Weber app and reconnects
  automatically when the handoff window ends.

The add-on publishes Home Assistant MQTT discovery sensors for each probe:

- Temperature in Fahrenheit
- Probe state
- Probe battery level

The hub exposes a fixed set of four probe slots. The update interval and the
phone handoff duration are adjustable from the panel; the probe count is not.
Empty slots publish a `No probe` state and a `null` temperature, so Home
Assistant entities remain stable as probes are connected and removed.

See [DOCS.md](DOCS.md) for full setup and troubleshooting.

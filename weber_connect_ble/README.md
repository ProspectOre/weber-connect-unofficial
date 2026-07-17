# Weber Connect BLE Bridge

Read-only, BLE-first Weber Connect Hub telemetry for Home Assistant, managed
from a built-in web panel.

## Highlights

- **Set Up My Hub** discovers the hub and configures phone coexistence with one
  physical confirmation and no manual identifiers.
- Four stable MQTT discovery probe slots expose temperature, state, and battery.
- Optional probe nicknames remain visibly tied to their slot, such as
  **Brisket · Probe 1**, without changing stable Home Assistant unique IDs.
- A compact one-screen control center shows connection source, all four probe
  slots, and MQTT health; new installs use a 10-second live read interval.
- **Use with Phone** releases Bluetooth to the official Weber app and reconnects
  automatically after the selected handoff window.
- Recommended onboarding creates a bridge-owned Weber Cloud companion so Home
  Assistant can keep reading probe telemetry during phone handoff or a BLE
  outage; **Local only** remains available as a fallback.
- Cloud-ready phone handoff recommends **Until I return**; without cloud, the
  saved timed reconnect fallback remains preselected.
- No Weber email/password login, Android traffic capture, phone secret
  extraction, or provisioning code is required for the normal companion-pairing
  flow.

BLE remains preferred. The recommended phone-coexistence path is read-only and
built on an undocumented Weber API that may change without notice; users can
choose local-only pairing during setup.

The official app can start a recipe while Home Assistant monitors the resulting
cloud probe snapshots. The add-on does not expose recipe instructions or send
cook-control commands.

See [DOCS.md](DOCS.md) for installation, cloud setup, phone handoff,
troubleshooting, the verified compatibility matrix, privacy, and limitations.

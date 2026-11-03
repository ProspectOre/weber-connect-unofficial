# Historical 3.0 follow-up and compatibility limits

[Production readiness](../../PRODUCTION_READINESS.md) retains the release gates
and navigation. This record preserves its version-bound evidence and limits;
see the [product matrix](../product-compatibility.md) for current candidate status.

Local telemetry described here is retired under ADR 0003. These historical
observations do not establish current runtime or controller acceptance.

Preceding evidence: [transport and endurance](production-3.0-transports.md), then
the [clean-install investigation](production-3.0-clean-install.md). References to
evidence "above" in the preserved narrative below refer to those records.

Version 3.0.2 adds two enabled-by-default connection-context entities without
changing the registered probe entities: a connectivity binary sensor with the
active Weber Cloud or Bluetooth method, and a timestamp for the most recent
successful update. Their state, icon, attributes, timestamp parsing, offline
visibility, connection-context setup contract, and cleanup of orphaned pre-3.0.1
connection-loss repairs are covered by automated tests. The physical endurance
evidence above validates the transport data that feeds them; the 3.0.2 entity
presentation itself is an automated validation claim.

The exact post-audit candidate was then installed and restarted for one final
proxy-only regression check. With hci0 disabled, the Weber app fully closed,
and tablet Bluetooth off, Home Assistant showed the ESPHome proxy as the hub's
only advertisement source at `-50 dBm`. This exposed a slow-proxy startup case:
forcing a fresh GATT discovery on the first session could consume the proxy's
connection window before notification setup completed. The transport now
prefers Home Assistant's cached GATT table on that first session, retries each
connection attempt once, and still falls back to fresh discovery when the cache
is stale.

After the deliberately power-cycled proxy returned, native diagnostics advanced
from 10 to 17 successful updates while the accumulated failed-update count
remained at four. The persistent local session stayed connected, retained the
live Probe 4 reading at `23.1 °C`, and reported no current error. The only new
integration warning was timestamped during the intentional proxy outage; none
appeared after recovery. The installation was then returned to **Phone + Home
Assistant**, tablet Bluetooth was restored, and the official app was brought
to the foreground. Fresh diagnostics reported a connected cloud transport,
six successful updates, zero failures, no current error, and the same `23.1 °C`
reading. This closes the first-session proxy-cache and bounded-retry regression
on the documented equipment.

Re-enabling the Yellow's local Bluetooth entry after that isolation test
required the full host reboot requested by Home Assistant. After reboot, hci0
returned in automatic passive-scanning mode and the ESPHome proxy returned to
active scanning with all three connection slots free. The recommended cloud
transport also recovered without intervention: final diagnostics recorded four
successful updates, zero failed or consecutive failed updates, no current
error, and a fresh Probe 4 reading of `22.3 °C`.

### Community compatibility report

On July 28, 2026, a user confirmed that version 3.0.3 works on a **Weber
Performer Deluxe Smart 57**, also known as the **Performer Premium Smart**, with
firmware `2.9.0.8076` in **Phone + Home Assistant** mode. The built-in **Grill
temperature** entity appeared and worked correctly alongside the external
probes. The user described the integration as working flawlessly; no debug log
was needed after the successful validation. This report is recorded in
[issue #24](https://github.com/ProspectOre/weber-connect-unofficial/issues/24).
The reporter also clarified that this model has two physical probe ports. That
feedback prompted the 3.0.4 model-aware entity behavior: Probe 3 and Probe 4
are created only when their slot numbers are present in decoded status.

This is useful model-specific compatibility evidence, but it is a community
report rather than the controlled pairing, restart, recovery, and endurance
matrix performed on the local Weber Connect Hub.

Two-proxy failover remains explicitly untested because a second proxy is not
available. It may not be described as verified.

The following also remain explicitly unverified on physical production
hardware: host-adapter-only pairing/endurance, real wall-clock token renewal
after the approximately 5.8-hour bearer lifetime, revoked-companion recovery,
prolonged Weber outages, hub Wi-Fi changes, additional models, firmware, and
regions beyond the evidence above, and installation from an actual `v3.1.0`
HACS release archive. Token renewal,
credential rejection, malformed-frame handling, and archive layout are covered
by deterministic tests or local candidate packaging, but those are not a
substitute for the named production scenarios.

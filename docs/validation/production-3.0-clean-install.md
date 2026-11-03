# Historical 3.0 clean-install investigation

[Production readiness](../../PRODUCTION_READINESS.md) retains the release gates
and navigation. This record preserves its version-bound evidence and limits;
see the [product matrix](../product-compatibility.md) for current candidate status.

Local telemetry described here is retired under ADR 0003. These historical
observations do not establish current runtime or controller acceptance.

The preceding [transport and endurance record](production-3.0-transports.md)
provides the context for the deletion and clean re-add below.

The production config entry was then deleted. Home Assistant removed the entry,
device, all registered probe-temperature entities, and stored private
configuration.
A clean re-add
found the hub through the ESPHome proxy and completed physical approval, but
Weber's association list never granted the newly generated companion access to
the hub, including after repeated checks beyond five minutes. This failure was
retained as evidence and investigated rather than treated as a successful clean
install.

A second clean pairing attempt on July 20 confirmed that first-time pairing is
supported through the active ESPHome proxy. The hub initially advertised but
did not beep or present an approval request. After its probe was unplugged and
the hub button was held for ten seconds to restart it, the ESPHome proxy
established the connection, the hub beeped, and local pairing returned
`CONFIRMED`. The
generated companion again remained absent from Weber's association list. This
separates the two behaviors: proxy-based physical pairing is verified on this
equipment; that run still did not prove clean Weber Cloud association.

Reviewing the proven 2.1 pairing path after those failures found a clean-slate
3.0 ordering regression. Version 2.1 registered the generated cloud companion
before presenting its ID to the hub over Bluetooth, allowing the hub to publish
the association during the approval session. The initial 3.0 config flow paired
locally first and registered the cloud companion afterward, after that one-time
association opportunity had passed. The native flow now restores the proven
cloud-registration-before-Bluetooth order and requires the exact hub to appear
in Weber's association list before saving an entry. A fresh production pairing
with this corrected order completed local approval and the full five-minute
cloud check, but the exact hub remained absent. Immediate isolation testing then
showed that the official app could read the hub and live probe over Bluetooth,
while the same app reported the hub **Offline** as soon as tablet Bluetooth was
disabled. The app showed a saved Wi-Fi network at one signal bar, and the hub's
Wi-Fi MAC was absent from the local ARP table. This run therefore cannot validate
or disprove automatic cloud association: the hub itself had no working cloud
path.

The hub's rear reset was then pressed briefly to reboot it without erasing its
configuration. With tablet Bluetooth disabled, the official Weber app retained
a live Probe 3 reading, proving that the hub's Weber Cloud path had recovered.
A fresh Home Assistant setup then exposed one restart-related ESPHome proxy
edge case: the hub advertised before its complete GATT service table was
available, and Bleak reported a missing Weber session characteristic. The
pairing transport now clears that stale discovery, reconnects up to three times
before presenting the approval request, and reports the exact recovery reason
instead of an unexpected setup failure. Two focused regression tests cover the
successful recovery and bounded failure paths.

The final clean-install run used the corrected cloud-registration-before-BLE
order and kept tablet Bluetooth disabled during pairing. Home Assistant
generated and registered a new private companion, paired through the ESPHome
proxy, received physical approval from the hub, and found the exact hub in
Weber's association list about 12 seconds after the cloud check began. Home
Assistant created the native device without a Weber account login. After tablet
Bluetooth was restored and the official app reopened, the app showed Probe 3 at
`74 °F` while Home Assistant simultaneously showed `73.9 °F`.

Final native diagnostics reported the recommended
`phone_and_home_assistant` mode, `cloud` transport, a connected socket, nine
successful updates, zero failed updates, zero consecutive failures, no current
error, and Probe 3 at `23.3 °C`. The device page contained stable entities for
the four slots reported by that hub. This closes the clean-install
cloud-association release blocker on the documented equipment and validates
the intended default setup end to end.

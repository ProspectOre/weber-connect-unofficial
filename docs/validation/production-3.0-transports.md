# Historical 3.0 transport and endurance evidence

[Production readiness](../../PRODUCTION_READINESS.md) retains the release gates
and navigation. This record preserves its version-bound evidence and limits;
see the [product matrix](../product-compatibility.md) for current candidate status.

Local telemetry described here is retired under ADR 0003. These historical
observations do not establish current runtime or controller acceptance.

Physical testing uses Home Assistant Yellow on Home Assistant 2026.7.2, a Weber
Connect Hub `2.0.3_7398`, the Weber Android app `2.10.0.2439` on a Samsung Galaxy
Tab A9+, and one active ESPHome proxy running ESPHome 2026.7.0. Matching phone
and Home Assistant cloud temperatures, physical-confirmation pairing, proxy
discovery, and direct proxy reads have all been observed on that equipment.

Discovery and physical-confirmation pairing passed through one active ESPHome
Bluetooth proxy running ESPHome 2026.7.0. Home Assistant identified that proxy
as the connection path. A later production test received live probe data while
Home Assistant reported the test ESPHome proxy as the hub's sole advertisement
source at -48 dBm. The integration then returned
successfully to the default phone-and-cloud mode.

On July 19, 2026, the host hci0 entry was disabled and the test proxy was the
only active Bluetooth route for an initial observation window. ESPHome recorded a
connection to the test hub, a successful status read, a normal
`reason=0x00` disconnect, and release of the proxy slot. One transient GATT
`status=133` attempt then recovered automatically on retry. This established
proxy-only routing, direct protocol compatibility, clean slot release, and
recovery from that transient controller failure before the final endurance
run.

The production entity check observed four probe slots on the documented Weber
Connect Hub. During an active sample Probe 3 reported `76.1 °F`; after the hub
slept, every registered probe entity remained visible as `Unknown`. Probe
state, type, and battery remain attributes of the temperature entity. hci0 was
restored after the proxy-only window, and the integration was returned
successfully to the recommended Phone + Home Assistant mode.

On July 20, 2026, the final persistent transports were deployed together to
Home Assistant 2026.7.2 and exercised against the same hub and proxy. In
Phone + Home Assistant mode, the companion WebSocket recovered after the
sleeping hub was woken, produced eight successful live updates with zero
consecutive failures, and recorded repeated incoming `0x80` and `0x83` frames.
After the complete proxy test and restoration of the default mode, a fresh
cloud session produced seven successful updates, zero failures, and a live
Probe 1 reading of `22.7 °C`.

For the final persistent proxy smoke test, hci0 was disabled and the ESPHome
proxy was the only active Bluetooth route. Once the Weber app was fully closed
and Bluetooth was disabled on the tablet, ESPHome opened one connection to the
test hub at 13:55:25 PDT and retained the active slot without a
disconnect for more than two minutes. Native diagnostics increased from eight
to fourteen successful updates while the same connection remained open;
Probe 1 changed from `23.1 °C` to `23.0 °C`, the failure count did not
increase, and consecutive failures remained zero. A deliberate config-entry
reload released the earlier proxy slot with `reason=0x00`. Before the tablet's
Bluetooth was disabled, competing app ownership caused transient reconnects,
including `reason=0x08`; the integration recovered automatically after the
phone path was released and the hub was woken. This validates the documented
setup instruction as well as proxy-only routing, persistent slot ownership,
fresh status requests, clean unload, and automatic retry on the tested setup.

The final proxy-only endurance run started at approximately 16:20 PDT on July
20 with the Weber app closed, tablet Bluetooth off, and hci0 disabled. The
ESPHome proxy was the sole active Bluetooth path. Over 61 minutes, native
diagnostics advanced by 350 successful updates and 16 transient failed
attempts. The final sample had zero consecutive failures, no current error, and
a live Probe 2 reading of `23.8 °C`. The proxy's Wi-Fi response time varied and
occasionally missed a ping, but the transport recovered without manual action.

Home Assistant was then restarted while hci0 remained disabled. Its HTTP UI
returned in approximately 26 seconds. After restart, the integration had
already completed nine successful updates, recovered from four transient
attempts, reported zero consecutive failures, and exposed a live Probe 2
reading of `23.2 °C`. No repair, re-pairing, config-entry reload, or manual
transport recovery was required. Home Assistant's Bluetooth page continued to
show the ESPHome proxy as the sole advertisement path.

The test installation was then returned to the recommended state: hci0 enabled,
Phone + Home Assistant selected, the probe entities reported by that hub, no
repair attention, and a healthy cloud transport.

All four app/cook combinations were exercised after restoration: app closed
with no cook, app open with no cook, app open with an active cook, and app
closed while the cook remained active. Cloud updates continued in every case.
During the active-cook checks, the app and Home Assistant matched after unit
rounding.

The final persistent companion-WebSocket endurance run started at approximately
17:56 PDT with the Weber app open and a recipe active. More than 70 minutes
later, the app still showed the same active cook at `75 °F`; Home Assistant
reported Probe 2 at `23.7 °C` (`74.7 °F`). Final diagnostics showed the socket
connected, 554 successful updates, 15 failed attempts accumulated since the
session began, zero consecutive failures, no current error, and a successful
update less than ten seconds before capture. This validates simultaneous app
use, active-cook temperature continuity, automatic transient recovery, and the
final persistent WebSocket lifecycle on the documented equipment.

Earlier prototype cloud testing completed 60 minutes 14 seconds with 356
successful updates, zero failures, and a mean interval of approximately 10.15
seconds. All 72 independent Home Assistant availability samples returned HTTP
200, and Home Assistant recorded no Weber warning or error. An active cook then
remained populated for at least 1 hour 47 minutes, including a full Home
Assistant restart, while the app displayed 76°F and Home Assistant 76.1°F.
Those runs validated the companion identity, decoded probe data, stable
entities, and simultaneous app use; they used the retired cook-history polling
path and therefore do **not** validate the final persistent WebSocket lifecycle.

The final persistent companion-WebSocket endurance row, proxy-only endurance
row, and proxy-only Home Assistant restart row have passed on the documented
equipment. During the proxy-only run, the ESPHome proxy was also deliberately
rebooted while the integration owned its GATT session. The integration
reconnected without a reload or repair; diagnostics advanced from nine
successful updates and zero failures before the reboot to 31 successful
updates, five transient failures, zero consecutive failures, and no current
error afterward. Probe 2 resumed at `23.8 °C`. This verifies recovery from one
proxy reboot on the documented equipment.

# Connected product coverage and user feedback

Scope checked October 16, 2026 (America/Los_Angeles). This is a worklist for
read-only Home Assistant telemetry, not a certification of every Weber product.
The unpublished RC4 candidate combines the transport work in
[PR #73](https://github.com/ProspectOre/weber-connect-unofficial/pull/73) with
Andrew Hunt's manual-cooking fix in
[PR #74](https://github.com/ProspectOre/weber-connect-unofficial/pull/74).

## Product applicability

Weber's [connected-product inventory](https://contact-emea.weber.com/hc/en-us/articles/18254325420573-Security-Updates-Support-Policy)
and [2026 announcement](https://www.weber.com/US/en/pr-release-NPD26.html)
establish which lines offer connected products. They do not establish our wire
compatibility. Generation, region, actual controller and firmware matter more
than the grill's family name. No marketing SKU selects a frame parser.

| Product line | Applicable path and present evidence | Remaining gate |
| --- | --- | --- |
| Original Connect Smart Grilling Hub | Saber setup and authenticated cloud telemetry; historical maintainer physical receipts. | Exact RC4 pairing, app coexistence, recovery and endurance. |
| SmokeFire | Contributor EX4 Gen 2 / `2.0.3_7398` RC3 full-cook and restart report; manual cooking fix integrated in RC4. | Physical RC4 regression, including cooking state and cloud gaps. |
| Searwood and Smoque | Connected pellet controllers are targets of manufacturer/GATT qualification and cloud status decoding. | Controller evidence, pairing, cloud association and full cook for each generation. |
| Spirit Smart | Legacy advertised model ID 7 is classified; newer controllers use observed manufacturer/GATT evidence. | Per-controller pairing, cloud readings and restart acceptance. |
| Genesis II / Genesis Smart | Legacy model ID 8 and separately qualified SiLabs path implemented; issue #65 is unresolved. | Reporter manufacturer/frame evidence, physical approval, cloud association and live readings. |
| Summit Smart | Connected gas controller is an applicability target; reported burner telemetry is read-only. | Actual advertisement/GATT, pairing, cloud and sensor acceptance; no actuator commands. |
| Performer Smart / Premium Smart | Historical community temperature report on `2.9.0.8076`; controller qualification applies. | Exact-generation RC4 setup, recovery and cook acceptance. |
| Kettle Smart Ring | Connect-connected charcoal accessory is an applicability target, including on non-smart kettles. | Actual controller evidence and complete hardware run; no airflow control. |
| Lumin Smart | Listed by Weber as Connect-based; no integration hardware evidence. | Product-specific transport, pairing and telemetry evidence. Ordinary Lumin does not imply connectivity. |
| Smart Hub Display and Booster / wireless charging boosters | Separate newer connected accessories; qualify actual controller, not legacy Hub assumptions. | Advertisement/GATT/cloud evidence and wired/wireless slot mapping. |
| Smart Probe and Grate Sensor / Smart Wireless Probe / Probe Plus | Direct portable service recognized; decoder tested offline. Cloud-reported probe slots are handled through a paired controller. | Direct setup stays blocked until compatible peer authentication is established. Linked-probe readings require hardware acceptance. |
| Pulse 1000/2000 and iGrill | Weber categorizes these as iGrill-based. A similar product name or an app enum does not establish Connect compatibility. | Separate protocol investigation; no iGrill compatibility claim. |
| Other non-connected Weber grills | Can be monitored through a compatible connected hub or probe accessory where available. | The ordinary grill itself supplies no assumed connected interface. |

For the newer Smart Hub's advertised two wired and four wireless probes, the
software no longer discards cloud-reported slots above four. A defensive ceiling
of 16 slots bounds decoding; it does not claim a device has 16 ports. Missing
capabilities keep the legacy two-slot default. Actual zero/one/two-slot counts
are respected, additional reported slots appear dynamically, and registered IDs
and nicknames survive reload. Wireless slot numbering still needs device evidence.

## Latest feedback and disposition

The [RC3 SmokeFire report](https://github.com/ProspectOre/weber-connect-unofficial/pull/71#issuecomment-6227317248)
records a 3.5-hour manual cook on HA `2026.10.1`, firmware `2.0.3_7398`, an active
ESPHome proxy and the Android app open. Discovery, pairing, cloud association,
temperatures, restart recovery, IDs and nicknames worked on that runtime.
That community observation belongs to RC3 and does not validate RC4.

| Feedback | RC4 disposition |
| --- | --- |
| Cooking off during an active manual SmokeFire cook; confirmed in #74. | Count an active appliance in a known cooking mode, without requiring burner or recipe sessions. Unknown and cleaning modes do not imply cooking. |
| Generic Hub model on SmokeFire. | Display the reported model or recognized family, with a conservative controller label when unknown. |
| Blocking certificate load during setup. | Construct the HTTPS opener lazily in the synchronous executor request path. |
| Deprecated unscoped device lookup. | Scope firmware updates to devices owned by the config entry, using APIs available since the supported HA baseline. |
| Fuel full/unknown churn, 223 changes in 3.5 hours. | Preserve explicit device unknowns and distinguish them from omitted fields; counters collect actual fuel reports and changes for diagnosis. No optimistic full reading. |
| Cloud drops, including two gaps of roughly 2–2.5 minutes. | Preserve bounded recovery and fresh-reading requirements; recovery durations and failure types support the next diagnosis. No claim that these gaps are fixed. |
| Genesis #65 framing failure. | Separate SiLabs classification and exchange implemented; no parser relaxation or success claim without the reporter's controller evidence. |
| EP-435-style thermometer #59, SKU 4553721 / `1.4.4_188`, no approval prompt. | Qualification and support-report path retained. No returned support report or successful pairing evidence; remains unresolved. |
| RC evidence fingerprint mismatch. | Preserve prior RC receipts; new runtime gets its own automated fingerprint and pending physical matrix. |

## Acceptance

For every applicable controller, record product/generation, firmware, integration
and HA versions, adapter/proxy and app platform. Check discovery, physical
approval, exact cloud association, fresh temperature/cook readings, app
coexistence, restart/recovery, stable entity IDs and endurance. Zero/one/multiple
probe variants need their own slot checks. Unreported capabilities create no
actuator controls. Pairing never authorizes cook, ignition, target, timer, burner,
Wi-Fi or firmware changes.

Use **Get help / report a problem** for a failure and add the last successful
stage. Share structural classification and counters, not addresses, appliance
IDs, secrets, raw packets or logs. All hardware gates for RC4 remain pending;
see [candidate readiness](validation/3.2.2rc4-readiness.md).

# Help test Weber Connect Unofficial

A report about one model and firmware helps us document what works and narrow
down failures. A normal cook or probe session is enough to contribute. See the
[release channels](../../README.md#choose-a-release) and
[hardware results](../product-compatibility.md).

## Install or roll back

- Keep your existing Home Assistant entry and named entities. You can report
  results on stable without upgrading. Make a Home Assistant backup before
  trying the beta.
- To try the beta, open **Weber Connect Unofficial → Redownload** in HACS, turn
  on **Show beta versions**, and choose the current beta from
  [GitHub Releases](https://github.com/ProspectOre/weber-connect-unofficial/releases).
- Restart Home Assistant and confirm the installed `integration_version` in
  **Settings → Devices & services → Weber Connect → Download diagnostics**.
- To roll back, return to **Redownload**, select the
  [latest stable release](https://github.com/ProspectOre/weber-connect-unofficial/releases/latest),
  and restart Home Assistant. Keep entries that already worked on stable.
  If setup used beta-only support, the device may need the beta to load.

## What helps most

| Device | Useful check | Where to report |
| --- | --- | --- |
| EP-series WiFi Thermometer, SKU 4553721 | RC14 pairing, live readings, and restart recovery passed on USB with firmware 1.4.5_200. Keep a working entry. If you are already setting up over ESPHome, share whether approval, setup, and fresh readings work. | [#59](https://github.com/ProspectOre/weber-connect-unofficial/issues/59) |
| Searwood 600 | With the existing entry, check whether HA reconnects at power-up and updates grill temperature and both probes during a normal beta cook with the Weber app open. Report which probe targets or other fields appear in each app. | [#96](https://github.com/ProspectOre/weber-connect-unofficial/issues/96) |
| SmokeFire EX4 Gen 2 | During a normal beta cook, watch for fuel going unknown while temperatures still update, false idle, or connection gaps. | [#71](https://github.com/ProspectOre/weber-connect-unofficial/pull/71) |
| Genesis with built-in controller | For a pending setup retry on the beta, report the model and firmware, whether the approval prompt appeared, and the last step reached. | [#65](https://github.com/ProspectOre/weber-connect-unofficial/issues/65) |
| Connect Smart Grilling Hub or another model | Check fresh readings, app coexistence, and recovery after restart. If you run it for an hour, include the runtime and longest gap you observed. | [Compatibility form](https://github.com/ProspectOre/weber-connect-unofficial/issues/new?template=compatibility_report.yml) |

## What to include

- Exact model and generation, firmware, integration and Home Assistant
  versions, HA install type, local Bluetooth adapter or ESPHome proxy/version,
  and Weber app platform/version when relevant. Mark unavailable details unknown.
- The steps you tried and what you saw. For setup, record discovery, physical
  approval, cloud association, and the first changing reading. For an existing
  entry, record whether reload or restart preserves entity IDs and fresh
  readings. For a gap, record start and recovery times with time zone, whether
  the Weber app still updated, and whether HA recovered on its own.
- For a pairing failure, use **Get help / report a problem**. For a telemetry
  problem, use **Download diagnostics** while the problem is visible, if
  practical, and again after recovery. Review attachments for credentials,
  QR codes, addresses, IDs, and other personal details before posting. Keep
  raw packets private.

Post in the existing thread for your model or symptom, or open a
[bug report](https://github.com/ProspectOre/weber-connect-unofficial/issues/new?template=bug_report.yml).
Label each stage you report as passed, failed, not tested, or not applicable.
For probe-slot tests, report the physical slots you actually used. Operate the
grill yourself and leave a working pairing in place. The integration reads
telemetry only during a cook. Stop the trial if the grill behaves unexpectedly
or you are unsure how to operate it safely. Direct portable Bluetooth probe
setup remains blocked until peer authentication is verified.

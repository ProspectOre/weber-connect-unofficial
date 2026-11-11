# OEM comparison: 10 November 2026

Scope: installed Android `com.weber.connect` **2.13.0.2704** on a Samsung
SM-X210, compared with repository candidate `3.3.0rc2` at `ec37220` and the
installed Home Assistant integration reporting **3.2.2**. The APK SHA-256 is
`7642614468495b2d2c7b1d74e708a25633e5b5a7c755defca96c8d05f57a23ab`.
The APK, reconstructed source and tablet captures remain outside the repository.
Decompilation was partial; readable mappings below do not prove every app path.

## Observed baseline

After the owner powered the Connect Hub with a probe, the OEM app showed Probe 3
at **77°F**, battery **85%**, Wi-Fi/cloud connected and no target selected.
Home Assistant's read-only state snapshot at **20:20 PST** matched both values,
with connection on, reading status receiving and cooking off. This is a brief
stable-version observation, not an installed-file audit, timed recovery test,
endurance run, pairing trial or acceptance of the 3.3 candidate. No cooking,
target-setting or pairing commands were sent during the comparison.

## Field provenance

Paths below are relative to the APK's reconstructed `com/weber` package.

| Field | OEM source and interpretation | Integration conclusion |
| --- | --- | --- |
| Fuel level | `sdk/cloud/ble/IncomingApplianceStatusResponseMessage`, `ApplianceController.FuelLevel`: appliance-status tag 20; 0 unknown, 1 full, 2 full-to-half, 3 half-to-quarter, 4 quarter-to-low, 5 low. | Existing enum mapping agrees. Absent data does not establish an empty tank. |
| Fuel percentage | Appliance-status tag 27; `model/FuelPercent` distinguishes reported percentages from estimates derived from the level enum. | Existing percentage and level sensors are separate. Do not manufacture a measured percentage from a level. |
| Cook intensity | `sdk/tlvmodels/CookSessionStatusTLV_TLVKt`, `model/SimpleIntensity`, `model/IdealizedIntRange`: status tag 11 is unsigned; level is `floor(raw * 10 / 256) + 1`. | Existing decoder agrees across the byte range. A missing entity requires checking whether the controller supplies that field. |
| Probe targets | `cook/GetCookTargetsInfoUseCase$observeWithResolution$1$2` resolves the recipe/program's current step and temperature ceiling/floor triggers. | Probe status carries current temperature and session references, not the target. RC2 lacked the program-data path; RC3 adds a bounded, correlated read of it. |
| Product identity | `model/ModelId`, `appliance/ApplianceModel` distinguish advertisement model codes from the app's product model strings. | The Searwood report's Hub label is unresolved. Do not assign a new model code based on a product name alone. |

Weber documents the 1–10 intensity as
[Searwood Manual Mode](https://contact-emea.weber.com/hc/en-us/articles/36966030002717-Understanding-Temperature-Modes-Set-Temperature-SmokeBoost-and-Manual-Mode).
The reported temperature-controlled cook therefore does not establish a failure
of Manual Mode intensity. Whether this controller emits tag 11 in either mode
still needs a sanitized capture; the documentation does not prove that behavior.

## Probe-target implementation

`sdk/cloud/ble/FetchProgramDetailsMessage` uses request **0x0B** with two payload
bytes: session type and session index. The old empty request was not an
implementation of this protocol. `IncomingProgramDetailsMessage` identifies
the session, program UUID and plan, then decodes `CookProgramPayload`.
Message version 11 changes plan IDs to 32 bits and step/prompt counts to 16 bits.
`CookProgramPayloadKt` decodes exit criteria and prompt triggers.
`SdkModelExtensionsKt.allTriggers` combines both sources for the active step.
Targets may be a single threshold or a range, and may change with the step.

RC3 binds responses to the authenticated controller, pending read window and
embedded probe/program/plan, then selects the active step. Session changes
invalidate the pending read. Session changes, expiry and
connection failures clear targets. Ranges have separate lower/upper sensors.
The cloud allowlist adds only the validated two-byte `0x0B` probe-program read
to empty status/capability reads (`0x05`, `0x07`, `0x0E`). It permits no target
writes or cooking controls. Source-derived regression fixtures cover versions
5–11, malformed data, correlation and target lifecycle. Live results are
recorded separately from these software checks.

The first live manual-alert trial exposed a valid all-zero program UUID. OEM
`SaberMessageFactory` calls this `SABER_PRIMITIVE_UUID` and uses step 2 and a
plan counter starting at zero. RC3 accepts this identity while still requiring
the active session and matching response. A regression preserves that case. A second live capture returned response
sequence 869 for request 8. OEM `CookSessionManager` also does not match request
and response sequences. RC3 therefore uses the embedded identity, with a bounded
request window. Its fixture preserves the observed 47-byte primitive program
(target 48.8°C, displayed by OEM as 120°F), without device identifiers or user text.

The live 70–100°F range alert stores its lower and upper thresholds in prompts,
while the exit criterion is a user action. RC3 now combines both trigger sources.
A sanitized 65-byte response fixture decodes 21.1–37.7°C, matching the OEM app
after its whole-degree Fahrenheit rounding. A successful direct response proves
the wire format, not reliable delivery to installed HA; see the targeted live
record for the final lifecycle results and earlier failures.

The range trial also exposed a false probe-type fallback: legacy plan tag 4
with value 2 labeled a wired probe as wireless. OEM `ProbeSessionStatusTLV_TLVKt`
keeps tag 4 (plan) and tag 19 (probe type) separate; `ProbeSessionStatusKt`
defaults an absent type to wired. RC3 follows that default and does not use plan
IDs as types. Present zero and malformed type data remain unknown.

The later live trial exposed queued stale status: reading one cook frame and
sleeping let newer session changes and program replies accumulate. RC3 now
consumes incoming messages between status polls with one serialized reader.
The final target/range edits, removals and active entry reload passed; measured
results are in the [targeted observation](3.3.0rc3-probe-target-observation.json).

## Pairing and remaining hardware evidence

`ApplianceKeyExchanger`, `IncomingPairingResponseMessage` and
`PairingResponseStatus` confirm the existing confirmed/rejected/timed-out
outcomes. The inspected source did not identify a narrow correction to the
integration's notification or key-exchange sequence. Parts of the OEM pairing
controller could not be reconstructed reliably; this is not proof of parity.

- [EP-435 report](https://github.com/ProspectOre/weber-connect-unofficial/issues/59): exact controller and physical approval behavior remain needed.
- [Genesis report](https://github.com/ProspectOre/weber-connect-unofficial/issues/65): sanitized transport structure, pairing and authenticated live readings remain needed.
- [Searwood report](https://github.com/ProspectOre/weber-connect-unofficial/issues/96): preserve its 3.2.2 cook evidence; collect model metadata, field presence, pairing failure structure and the missing restart/reconnect/coexistence outcomes.

The [compatibility registry](../support/compatibility.json) owns the per-product
dispositions. This comparison establishes no new physical acceptance gates.

# Hardware trial procedure

Use this procedure for an opt-in, manually operated compatibility observation.
It records one exact device and software combination; it does not certify a
product family. See the [compatibility inventory](../product-compatibility.md)
and submit results through the [compatibility report form](../../.github/ISSUE_TEMPLATE/compatibility_report.yml).

## Before testing

Record the product family, exact model/generation, controller identity as shown
by the product (never its address), firmware, integration release, Home Assistant
version/install type, Bluetooth adapter or proxy and version, and Weber app
platform/version. If a value is unknown, write `Unknown`; do not infer it from a
marketing SKU. Use the exact candidate release under test. A branch, local build,
or source test is not a published release.

The tester operates the grill and approves pairing in the device's normal physical
workflow. The integration and test procedure issue no ignition, cook, burner,
temperature-target, timer, firmware, or network-configuration commands. Stop if
the device behaves unexpectedly or safe manual operation is unclear.

## Run and record each stage

For every stage, record exactly one result: `passed`, `failed`, `not_tested`, or
`not_applicable`. Add a short observation and the failure stage. Unknown results
remain unverified. Do not mark a stage passed from logs, simulation, source review,
or a previous release.

1. **Discovery:** find the intended controller and confirm the reported family.
2. **Physical approval and setup:** approve pairing on the device when prompted;
   record whether approval appeared and setup completed.
3. **Cloud identity:** confirm the intended appliance was associated. Do not
   share account details, IDs, addresses, credentials, or tokens.
4. **Fresh readings:** confirm current temperatures and reported state change
   while observing the physical device. Mark stale, absent, or unknown separately.
5. **App coexistence:** with the Weber app open on its recorded platform, confirm
   Home Assistant continues receiving fresh readings and neither client changes
   grill settings.
6. **Reload and restart:** reload the integration, then restart Home Assistant.
   Confirm setup recovers and entity IDs/names remain stable.
7. **Recovery:** after a naturally occurring disconnect or an explicitly safe,
   manually initiated reconnect, record time to the next fresh cook status and
   whether fresh readings resume. Do not call appliance-only frames recovery.
8. **Cook or probe session:** run a complete manually controlled cook where safe
   and practical; otherwise test a representative probe session and mark full-cook
   acceptance `not_tested`.
9. **Endurance:** record elapsed time and any gaps, stale readings, unknown fuel,
   entity changes, or disconnects. Session counters are local diagnostic counts,
   not independent measurements or proof of a cause.

## Focus cases

- **SmokeFire:** include a full manual cook when practical. Track fuel as reported,
  including explicit `unknown`; compare timestamped fresh readings with the grill
  and app. Report gaps and recovery duration without treating counters as mock
  measurements or masking unknown as full.
- **Genesis / issue #65:** record the exact built-in controller and whether
  approval appeared. If setup stops at response framing, report that failure
  stage; do not try undocumented frame workarounds.
- **EP-435 / issue #59:** include model/SKU and thermometer firmware if visible,
  whether an approval prompt appeared, and the last successful stage. Pairing
  remains unverified until a tester records the actual outcome.
- **Probe slot mapping:** separately record zero, one, multiple wired, and wireless
  slots only when present. Confirm readings and stable entity IDs through reload
  and restart. Do not infer wireless numbering from the advertised slot count.
- **Direct probe setup:** stop at the authentication gate unless a compatible
  peer-authentication method has been established. Discovery is not authorization.

If a candidate trial fails, preserve the exact release and failure stage. Roll
back through Home Assistant's normal version/backup process; keep the config
entry and its identity so a retry can verify stable IDs. Report through the
[bug report form](../../.github/ISSUE_TEMPLATE/bug_report.yml) or compatibility
form. Submission is a user action; the integration does not contact Weber or
create reports automatically.

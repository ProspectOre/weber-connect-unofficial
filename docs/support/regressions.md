# Field report to regression test

Convert a support report into a test only after preserving its provenance and
separating observed evidence from reconstruction. Use the
[bug report form](../../.github/ISSUE_TEMPLATE/bug_report.yml), the
[compatibility form](../../.github/ISSUE_TEMPLATE/compatibility_report.yml), and
the [hardware trial procedure](hardware-trials.md) as intake records.

## Intake record

For each report, capture a source URL or private report reference, evidence type,
exact controller/model and firmware, integration release, Home Assistant version,
adapter/proxy and app context, last successful stage, failure stage, expected
behavior, and observed behavior. Mark missing facts `Unknown`. Do not put raw
tokens, account identifiers, device IDs/addresses, private keys, raw logs, packet
bytes, or customer data in fixtures, test names, or committed documentation.

Classify evidence explicitly:

- **Source-captured:** an actual frame or behavior recorded from the device with
  permission and enough provenance to identify its context. Store only a reviewed,
  minimized fixture where policy and consent allow it.
- **Sanitized reconstruction:** a minimized fixture derived from a described
  observation, with its source URL and transformation noted. It tests the stated
  behavior, not the exact undocumented wire representation.
- **Synthetic:** a deliberately constructed case. Label it synthetic; it proves
  only that the code handles that constructed input.

Never describe a reconstructed or synthetic fixture as captured hardware proof.
Keep source evidence and fixture provenance in the test/docstring or adjacent
fixture metadata so later edits do not silently turn a hypothesis into fact.

## Build the regression

1. State the expected behavior and the smallest failing condition in plain terms.
2. Add a minimized fixture for the reported failure. Add adjacent negative cases
   for absent fields, explicit unknowns, malformed/truncated fields, invalid
   routes, cross-device identity, and rejected authentication where relevant.
3. Assert both the user-visible result and the safety boundary: no guessed full
   fuel, no stale value presented as fresh, no cross-device association, no
   credential disclosure, and no accepted malformed frame.
4. Run the targeted parser/transport test, then the relevant native suite and
   normal lint/type checks. Record commands and results separately from hardware
   acceptance.
5. Link the candidate and regression to the originating report. Leave physical
   verification pending until a tester submits the exact hardware/runtime result.

Do not relax parser validation merely to make a guessed fixture pass. First
identify the observed protocol shape and what the existing parser rejects. For
Genesis issue #65, the available report says required notification/session
characteristics were absent and a later response hit `invalid transport frame`;
it did not include the response bytes. Do not invent a frame fixture and call it
captured proof. A synthetic negative test may still assert that unknown framing
is rejected safely, while the hardware question remains open.

Diagnostics such as fuel report counts and recovery durations are local
observations. Compare them with timestamped fresh readings and the tester's
runtime notes. They do not establish measurement accuracy, cause, or a fix by
themselves.

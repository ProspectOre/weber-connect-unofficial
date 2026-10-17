# Support triage

Use the weekly read-only inventory to turn user reports into an owned next step.
The support script reads GitHub issues and pull-request discussion, including
reports posted as PR comments. In particular, inspect [SmokeFire PR #71's cook
report](https://github.com/ProspectOre/weber-connect-unofficial/pull/71#issuecomment-6227317248),
not only issues titled as bug reports.

```sh
python scripts/support.py triage --github OWNER/REPO --output PATH
```

The command creates a local Markdown artifact. It does not open, comment on,
label, assign, close, or otherwise change GitHub records. Review the generated
artifact for private details before sharing it.

## Review and disposition

For each open report or relevant PR discussion, record:

- source URL and issue/PR number;
- owner or `unassigned`, current status, and last meaningful update;
- affected product/controller and exact release/runtime when known;
- last successful stage and failure stage;
- one concrete next step, its owner, and the evidence needed to complete it;
- candidate, test, or source change linked to the report, if one exists.

Prioritize authentication and identity boundaries, unsafe or misleading data,
freshness/recovery failures, setup blockers, then regressions and usability.
Repeated reports or a high impact do not establish root cause; link related reports
while retaining each affected hardware/runtime combination.

Use an acknowledgement target of two business days as a goal, not a service
guarantee. If the next step requires hardware, mark it waiting on hardware and
state the precise trial needed. Do not stale-close an unresolved report because
the reporter has not tested a candidate. Do not send messages or post comments
as part of generating or reviewing the inventory.

## Closure evidence

Close or mark resolved only after the specific claim is supported. A hardware
compatibility defect requires a fix candidate verified on the affected hardware
and the relevant lifecycle stage. Source review, unit tests, CI, a merge, and a
published version are distinct evidence; record each separately. A release does
not prove deployed behavior, and success on one controller does not establish
family-wide support.

For a fix, link the exact candidate/release, test result, runtime and hardware
receipt, and any remaining limits. For an issue that cannot be reproduced, retain
it as unresolved or waiting for evidence and state what would make it actionable.
Never invent a reporter result, close based only on silence, or rewrite a failed
or unknown stage as passed.

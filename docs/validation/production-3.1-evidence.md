# Historical 3.1 release evidence

[Production readiness](../../PRODUCTION_READINESS.md) retains the release gates
and navigation. This record preserves its version-bound evidence and limits;
see the [product matrix](../product-compatibility.md) for current candidate status.

The local 3.1.1 release candidate gate passes 130 tests with 95.66% combined
statement/branch coverage against the Home Assistant 2026.7.2 test framework.
Ruff, formatting, strict mypy, Bandit, dependency audit, Actionlint, release
contract validation, and diff-integrity checks all pass locally. Hassfest and
HACS validation remain required hosted gates on the exact release commit.

The historical automated results are retained in
[`docs/validation/3.1.1-rc-automated.json`](3.1.1-rc-automated.json).
The historical live assertions are retained in
[`docs/validation/3.1.1-rc-physical.json`](3.1.1-rc-physical.json)
without device identifiers. The original 3.0 transport and endurance evidence
remains in the adjacent 3.0 validation files.

On August 11, 2026, the exact 3.1.0 candidate was deployed to Home Assistant
2026.8.1 on Home Assistant Yellow and loaded after a measured full Core
restart. A Weber Connect Hub running `2.0.3_7398` connected through Weber Cloud
and exposed a live hub battery reading of 65% while charging, Wi-Fi signal at
-35 dBm, connected Wi-Fi and cloud states, idle device state, firmware
metadata, and a live probe reading of 24.3 °C. Diagnostics recorded five
successful updates, zero failed updates, no current error, and no Weber warning
or error log entries.

A config-entry reload required no host restart, retained the established entity
identities, reconnected automatically, and produced a later live sample of 70%
battery while charging and -37 dBm Wi-Fi with three successful updates and zero
failures. The live run also found that an idle wired probe reports zero-valued
countdown fields. The entity guard was tightened so those fields do not create
a misleading countdown until remaining or prompt time is positive or the probe
session is active, paused, or preheating. The corrected candidate was restarted
and reloaded again, and the temporary test entity was removed from the registry.

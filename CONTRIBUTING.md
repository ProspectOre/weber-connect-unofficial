# Contributing

## Development setup

Use Python 3.14.2 or newer. The [test matrix](.github/ha-test-matrix.json)
pins matching Home Assistant, test plugin and transport dependencies for the
minimum and current full-suite lanes. Install each in a separate environment:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python scripts/ha_compatibility.py requirements --lane minimum > /tmp/weber-ha-requirements.txt
python -m pip install -r requirements-runtime.txt -r /tmp/weber-ha-requirements.txt \
  bandit==1.9.4 mypy==1.19.1 pip-audit==2.10.0 ruff==0.15.22
```

Repeat with `--lane current` in a separate environment. The `latest` lane runs
imports and pure-protocol smoke checks when the full HA test plugin lags behind
the latest core release; it does not establish full-suite compatibility.

Run the local gate:

```bash
ruff check custom_components tests_native scripts
ruff format --check custom_components tests_native scripts
mypy --python-version 3.14 --strict --ignore-missing-imports custom_components/weber_connect
bandit -q -r custom_components/weber_connect scripts
pytest -q --asyncio-mode=auto tests_native --cov=custom_components/weber_connect --cov-branch --cov-report=term-missing --cov-fail-under=100
python scripts/validate_release.py
python scripts/support.py validate
python scripts/support.py render --check
pip-audit --requirement requirements-runtime.txt --no-deps --disable-pip
```

Hassfest and HACS validation run in GitHub Actions.

## CI routing

This public repository uses standard GitHub-hosted Ubuntu runners for CI,
CodeQL, and review jobs. Standard runner minutes are
[free for public repositories](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
Required tests, HACS, Hassfest, security analysis, and exact-head review remain
merge requirements. Physical pairing and appliance acceptance are separate.

The retired hosted-runner policy workflow sampled private-repository minute
allowances and forced hosted-runner variables off. CI routing is defined in
workflow source without that billing switch or a local runner fallback.
Larger runners are billed even for public repositories; artifact storage and
cache allowances have separate billing boundaries.

## Design constraints

- Use Home Assistant's documented Bluetooth manager. Never connect directly to
  an ESPHome proxy, copy a proxy key, or read Home Assistant `.storage`.
- Re-resolve the best adapter or proxy on connection retry and disconnect every
  GATT client in `finally`.
- Keep live telemetry on the authenticated cloud companion. Bluetooth is for
  physically confirmed setup only until peer-authenticated local status is
  documented and validated.
- Preserve physical confirmation for pairing.
- Never request the user's Weber email/password or extract phone secrets.
- Cloud authentication alone is insufficient; associate and scope access to
  the paired appliance.
- Remote commands, Wi-Fi configuration, ignition, recipe installation/start,
  target changes, and grill-mode control are out of scope for 3.0.
- Stable unique IDs must not depend on which adapter or proxy is selected.

## Compatibility reports

Success and failure reports are equally useful. Include:

- integration and Home Assistant versions;
- Home Assistant installation type;
- Weber product name and firmware version;
- official app platform/version and account country or region;
- local adapter or ESPHome proxy model and ESPHome version;
- whether discovery, physical pairing, cloud association, and phone + Home
  Assistant telemetry each succeeded.

Do not post MAC addresses, appliance or companion IDs, config entries, device
passwords, bearer tokens, email addresses, packet captures, or unredacted
diagnostics. Redact identifiers consistently when a sequence needs them.

Add a regression test for every new protocol shape. Update the physical matrix
only after the path was exercised on real hardware.

Use the structured bug or compatibility form on GitHub. The
[hardware trial checklist](docs/support/hardware-trials.md) defines opt-in
physical evidence; [field regressions](docs/support/regressions.md) keep
reported behavior reproducible without inventing packets. The
[triage process](docs/support/triage.md) includes reports in PR comments.
Weekly GitHub checks generate a read-only triage artifact and refresh HA
compatibility checks; they do not post replies or close issues.

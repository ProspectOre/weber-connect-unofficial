# Contributing

## Development setup

Use Python 3.14, matching Home Assistant 2026.7:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install \
  bandit==1.9.4 \
  homeassistant==2026.7.2 \
  mypy==1.19.1 \
  pip-audit==2.10.0 \
  pytest==9.0.3 \
  pytest-cov==7.1.0 \
  pytest-homeassistant-custom-component==0.13.346 \
  ruff==0.15.22
```

Run the local gate:

```bash
ruff check custom_components tests_native scripts
ruff format --check custom_components tests_native scripts
mypy --python-version 3.14 --strict --ignore-missing-imports custom_components/weber_connect
bandit -q -r custom_components/weber_connect scripts
pytest -q --asyncio-mode=auto tests_native --cov=custom_components/weber_connect --cov-report=term-missing --cov-fail-under=80
python scripts/validate_release.py
pip-audit --requirement requirements-runtime.txt --no-deps --disable-pip
```

Hassfest and HACS validation run in GitHub Actions.

## Design constraints

- Use Home Assistant's documented Bluetooth manager. Never connect directly to
  an ESPHome proxy, copy a proxy key, or read Home Assistant `.storage`.
- Re-resolve the best adapter or proxy on connection retry and disconnect every
  GATT client in `finally`.
- Keep **Phone + Home Assistant** as the default. Local fallback must remain explicit
  because it may take the hub's single Bluetooth connection from the phone.
- Preserve physical confirmation for pairing.
- Never request the user's Weber email/password or extract phone secrets.
- Cloud authentication alone is insufficient; associate and scope access to
  the paired appliance.
- Remote commands must remain opt-in, narrowly allowlisted, range-validated,
  and covered by protocol tests. Wi-Fi configuration, ignition, recipe
  installation/start, target changes, and grill-mode control are out of scope.
- Stable unique IDs must not depend on which adapter or proxy is selected.

## Compatibility reports

Success and failure reports are equally useful. Include:

- integration and Home Assistant versions;
- Home Assistant installation type;
- Weber product name and firmware version;
- official app platform/version and account country or region;
- local adapter or ESPHome proxy model and ESPHome version;
- whether discovery, physical pairing, cloud association, phone + Home Assistant,
  local reads, and proxy failover each succeeded.

Do not post MAC addresses, appliance or companion IDs, config entries, device
passwords, bearer tokens, email addresses, packet captures, or unredacted
diagnostics. Redact identifiers consistently when a sequence needs them.

Add a regression test for every new protocol shape. Update the physical matrix
only after the path was exercised on real hardware.

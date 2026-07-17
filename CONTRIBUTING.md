# Contributing

## Development Setup

Create a virtual environment and install the same dependencies used by CI:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r weber_connect_ble/app/requirements.txt
python -m pip install -r requirements-dev.txt
```

## Development Checks

Run the complete local gate before opening a pull request:

```bash
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python scripts/validate_release.py
.venv/bin/coverage run -m unittest discover -s tests
.venv/bin/coverage report
```

## Design Constraints

- BLE remains the preferred transport and only one bridge BLE operation may own
  the hub at a time.
- Cloud behavior remains opt-in and read-only. New cloud code must not start
  recipes, modify targets/timers, configure Wi-Fi, or issue grill controls.
- Cloud authentication tests must verify appliance-scoped access; a successful
  companion login alone is not enough.
- Generated companion credentials must remain private and must never appear in
  public status payloads or logs.
- Preserve the physical-confirmation requirement for companion pairing.
- Add malformed, unauthorized, timeout, pagination, and stale-data tests when
  changing protocol boundaries.

## Pull Requests

- Keep captures, credentials, local runtime JSON, and appliance identifiers out
  of commits.
- Redact logs before attaching them.
- Update `weber_connect_ble/CHANGELOG.md` and every affected user document for
  user-visible changes.
- Prefer small, focused patches and explain any private-API assumptions.

## Compatibility Reports

Reports from hardware and account combinations outside the documented test
matrix are welcome, whether they succeed or fail. Open an issue with:

- Add-on and Home Assistant versions.
- Home Assistant host type and Bluetooth adapter or proxy model.
- Weber hub product name and firmware version, when visible in the official
  app.
- Official app platform and version, plus the account region or country.
- Whether local pairing, cloud association, phone handoff, and live probe
  updates each succeeded.
- Redacted add-on logs covering the failed step.

Never post MAC addresses, appliance or companion identifiers, pairing exports,
device passwords, bearer tokens, email addresses, packet captures, or files
from `/data/weber-connect-bridge`. Replace identifiers consistently when they
are needed to explain a sequence.

Pull requests for additional models or firmware should preserve existing
behavior, add a regression test for the new protocol shape, and update the
verified matrix only when the path has also been exercised on physical
hardware. A report without a code change is still useful and does not need a
pull request.

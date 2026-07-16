# Contributing

## Development Setup

Create a virtual environment and install the runtime and development
dependencies, matching CI:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --require-hashes -r weber_connect_ble/app/requirements.txt
python -m pip install -r requirements-dev.txt
```

## Development Checks

Run the same checks CI runs before opening a pull request:

```bash
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python scripts/validate_release.py
.venv/bin/coverage run -m unittest discover -s tests
.venv/bin/coverage report
```

The add-on should remain read-only unless a future release explicitly designs,
documents, and reviews control-command safety.

## Pull Requests

- Keep private captures and credentials out of commits.
- Include logs with secrets redacted.
- Update `weber_connect_ble/CHANGELOG.md` for user-visible changes.
- Prefer small, focused patches.

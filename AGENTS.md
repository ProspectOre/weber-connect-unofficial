# AGENTS

Keep physical acceptance separate from source review and CI.

## Apple tool capabilities

- Inherit `/Users/alec/Dev/AGENTS.md`. Prefer discovered native `xcode-tools` for builds, tests, previews, and diagnostics; use XcodeBuildMCP or shell when unavailable or insufficient.
- Use official Apple documentation for API references when `DocumentationSearch` is absent. Rediscover tools each session; do not impose a fixed tool count or version requirement. Dated bridge evidence lives in `/Users/alec/Dev/wiki/workflows/xcode.md`.

## CI, review, deploy

- Follow the [delivery contract](https://github.com/ProspectOre/wiki/blob/main/workflows/delivery-contract.md); `python3 .github/scripts/delivery-contract.py .` runs first in `ci` and must print `delivery-contract: ok`.
- `ci.yml` is the required check `ci`: ruff, mypy, bandit, native suite at 100% branch coverage, release contract, support registry, pip-audit, actionlint, hassfest, HACS. `codeql-analysis.yml` runs CodeQL on the same triggers. `compatibility-watch.yml` is the weekly nightly that re-resolves Home Assistant pins; dispatch it to refresh early.
- Public repository: `ubuntu-latest` only; `tests_native/test_claude_runner_trust.py` rejects any other runner label.
- Test locally: `ruff check custom_components tests_native scripts && pytest -q --asyncio-mode=auto tests_native --cov=custom_components/weber_connect --cov-branch --cov-fail-under=100`.
- Release: no workflow; run `python scripts/validate_release.py`, then tag and publish the GitHub release for HACS by hand.

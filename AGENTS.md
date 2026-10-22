# AGENTS

Keep physical acceptance separate from source review and CI.

## Workspace and attribution

- Inherit `/Users/alec/Dev/AGENTS.md` when present (native `xcode-tools` preference included); this file adds repository facts only.
- No AI attribution: no AI co-author or `Assisted-by` trailers, agent session links, "Generated with/by" footers or AI sign-offs in commits, PR titles or bodies, reviews or comments; commit as the account owner; name branches by purpose. `.github/scripts/attribution.py` enforces it in `ci`.

## CI, review, deploy

- Follow the [delivery contract](https://github.com/ProspectOre/wiki/blob/main/workflows/delivery-contract.md); `python3 .github/scripts/delivery-contract.py .` runs first in `ci` and must print `delivery-contract: ok`.
- `ci.yml` is the required check `ci`: ruff, mypy, bandit, native suite at 100% branch coverage, release contract, support registry, pip-audit, actionlint, hassfest, HACS. `codeql-analysis.yml` runs CodeQL on the same triggers. `compatibility-watch.yml` is the weekly nightly that re-resolves Home Assistant pins; dispatch it to refresh early.
- Public repository: `ubuntu-latest` only; `tests_native/test_claude_runner_trust.py` rejects any other runner label.
- Test locally: `ruff check custom_components tests_native scripts && pytest -q --asyncio-mode=auto tests_native --cov=custom_components/weber_connect --cov-branch --cov-fail-under=100`.
- Release: no workflow; run `python scripts/validate_release.py`, then tag and publish the GitHub release for HACS by hand.

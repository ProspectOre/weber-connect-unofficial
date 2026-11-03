# Changelog

## 3.3.0rc2 — controller-family beta

- Raise the websockets runtime requirement from 17.1 to 17.3. Integration code is
  unchanged from 3.3.0rc1.
- Bind new automated and pending physical receipts to the changed runtime; rc1
  evidence does not carry over.

## 3.3.0rc1 — controller-family beta

- Move the broader 3.2.2 prerelease work to the 3.3 version line, leaving stable
  3.2.2 for the isolated read-only cloud hotfix from 3.2.1.
- Preserve RC6 behavior and its status/capability-only outbound boundary.
- Keep hardware evidence version-bound; the new candidate does not inherit
  full physical acceptance from historical RC6 observations.

Earlier prerelease and stable changes are preserved in the
[RC6 changelog](https://github.com/ProspectOre/weber-connect-unofficial/blob/v3.2.2rc6/CHANGELOG.md)
and the [published releases](https://github.com/ProspectOre/weber-connect-unofficial/releases).

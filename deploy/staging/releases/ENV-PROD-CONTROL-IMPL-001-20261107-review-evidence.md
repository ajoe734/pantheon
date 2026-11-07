# ENV-PROD-CONTROL-IMPL-001 — release manifests 103/104 review evidence (2026-11-07)

Scope: source only. Two immutable manifests added; runbook section 6 corrected. No cloud access, dispatch, settings or secrets. Nothing in the Prod sequence has been executed.

## Reviewer checks
| File | file sha256 | canonical manifest sha256 |
|---|---|---|
| deploy/staging/releases/pantheon-20261107-103.json | 1e566d6c919584e6834fe428d49023c2b184a91e5c388e8af55888692cf6ef09 | sha256:e9a6763b4ee68a28cfb0814e8affd5dc00fec706f6229b2ebd393fe840b4ebb1 |
| deploy/staging/releases/pantheon-20261107-104.json | 3fabdc0d9e04dacff05d55005c6e04b0800d9ddf39ef8ad983b935956b6cb405 | sha256:11042a51a76a23e7eab6394326a7fde618fd46c3a776aa593fc676317dec70d1 |

Byte equality: `cmp` each file against `/tmp/pantheon-prod-release-20261107/manifests/<same name>`.
Hash: `python3 scripts/deploy/release_manifest.py hash --manifest <file>`.
Validate: `python3 scripts/deploy/release_manifest.py validate --manifest <file> --require-dev-verified` (exit 0 for both when authored).

## Author test results
- tests/deployment/test_production_release.py: 95 passed.
- tests/deployment/test_staging_release.py: all pass run serially except `test_dsn_query_routing_override_rejected` and `test_two_consecutive_simulated_releases_zero_to_zero_and_prod_preflight_alignment`. Both fail identically on the base commit with the new files removed (local environment), so not caused by this change. Parallel xdist runs interfere and are not valid evidence.

## Baseline correction: 105/106 (2026-11-07)

Supersedes 103/104 as the Ops execution plan; 103/104 remain unchanged as immutable historical declarations (not staged, not deployed). Reason: A (2638/979) has FE auth fix but lacks Registry 6992 and returns Viewer Fleet 403, so it is not a healthy first baseline. Scope: two metadata copies plus runbook section 6 and this file. No scripts, workflows, tests or product code changed. No cloud access, dispatch or secrets.

| File | file sha256 | canonical manifest sha256 |
|---|---|---|
| deploy/staging/releases/pantheon-20261107-105.json | d02b0733621b4900a581717d8b8271b35aadf1e9e388fffc4b835d971d22e586 | sha256:6a9cd147e4e34e5810e3650e85302c1acb62dca71718258c1743957daaaa476b |
| deploy/staging/releases/pantheon-20261107-106.json | 147d0accb6c07d3258d51076514a4c9e38166015c03f719b83f8efca9f3f08d6 | sha256:53ecc56ec09c69b82963dbeda58913cb28b4e81e231d8ba4d7f9f153a56dfbd8 |

Source: `/tmp/pantheon-prod-release-20261107/prepared-same-B-baseline-candidate/` (copied byte-for-byte; `release_manifest.py hash` and `validate --require-dev-verified` exit 0 for both). Both reuse BFF d77e03a05 / FE b7cd3d847, FE artifact d45f1d34. 106 rollback is 105; 105 rollback refs are historical 103 artifact metadata only.

Status: 105 and 106 are unqualified until each has actual Stage evidence for the 3 accounts. Prod bootstrap, restore, FRESHBOOT, two real rollbacks and the 1800 s observation are all pending (external_wait). The earlier Production 95-pass and serial Stage results (2 documented baseline failures, unrelated) are preserved and not rerun.

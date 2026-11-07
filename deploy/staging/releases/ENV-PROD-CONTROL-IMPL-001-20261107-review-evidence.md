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

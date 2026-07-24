# AG-COMPAT-002-GATE — Finalize and enforce the Agora cross-repository compatibility gate

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Claude2
Depends on: `AG-COMPAT-001-FE`

## Objective

Replace the pending/zero-placeholder dev compatibility manifest with an exact
Pantheon/execute-plans pair and enforce it before Agora deployment acceptance.

## Owned scope

- `docs/contracts/agora/dev-compatibility-manifest.json`
- Agora compatibility verifier and deployment/integration-gate wiring
- focused positive and negative compatibility tests

## Required work

1. Consume the backend and frontend machine-readable handoffs.
2. Verify both commits are reachable from their respective `dev` branches.
3. Populate exact runtime, generated-contract, bundle, OpenAPI, and generated
   type hashes; reject placeholder and mismatched values.
4. Make the integration/deployment gate stop before switch when compatibility
   is pending or rejected.
5. Preserve rollback to the last accepted compatible pair.

## Acceptance

- Manifest status is accepted only for one exact pair with all hashes non-zero.
- Tampered type, OpenAPI, bundle, commit, and branch-reachability fixtures fail.
- A pending/rejected candidate cannot change the hosted symlink/manifest.
- Rollback test restores the prior accepted pair.
- PR merges to `dev`; the gate runs successfully for the intended pair.

## Delivered evidence (2026-07-24)

- Pantheon PR
  [#4016](https://github.com/ajoe734/pantheon/pull/4016), merge commit
  `e2f7e7356b517844a946b780b373492d98af8c30`, pins the accepted manifest
  to frontend `e4399e3ec68f882ace35d0349e6597cdd101525f` and BFF
  `00b38f41ec51296762d502c4bd5732f95ccf2953`. Branch checks passed, all
  compatibility hashes are non-zero, and both commits are reachable from
  their protected `dev` branches.
- execute-plans integration-gate run
  [30003411349](https://github.com/ajoe734/execute-plans/actions/runs/30003411349)
  attempt 3 succeeded for that exact pair and emitted immutable candidate,
  release-identity, and integration-evidence artifacts.
- Deploy run
  [30056451511](https://github.com/ajoe734/execute-plans/actions/runs/30056451511)
  attempt 1 observed an intervening live BFF change to `f4f5f8f...` and
  failed before the hosted switch. The previous release and manifest
  remained live. Pantheon run
  [30056916386](https://github.com/ajoe734/pantheon/actions/runs/30056916386)
  then restored only the strict BFF component to the gated `00b38f41...`
  commit and passed the exact-version and restart-persistence probes.
- Deploy run `30056451511` attempt 2 then succeeded. The hosted
  `deployment.json` reports pair
  `5b5d84cb24e4f7280a02924591d01f570f3d73d791f5761c98dc67a571e9a55f`,
  compatibility manifest digest
  `494980f204f0af21effc018ebbba657c1027b3052e984577833dfa46ab360bb3`,
  the exact frontend/BFF commits, `deploymentState: accepted`,
  `deploymentProfile: read-only`, `VITE_BFF_MODE: live`,
  `VITE_BFF_FALLBACK: strict`, and both real and stub writes disabled.
  Live `/bff/version` independently reports the exact `00b38f41...` BFF
  commit with strict auth and MFA required.
- The sealed attempt-2 deploy evidence records `26 passed, 0 failed` in the
  production controller harness. It covers pending/rejected/mismatched
  evidence preserving the current symlink and manifest, post-switch and
  durable-evidence failures restoring the previous release, a manual
  rollback drill restoring and re-probing the exact previous release, and
  compare-and-swap protection against an external live switch.

## Owner finalization verification (2026-07-24)

- Claude2 independently approved the exact manifest, negative gates,
  gate-before-switch behavior, rollback/no-switch harness, workflow runs, and
  hosted read-back.
- `/home/lupin/pantheon/.venv/bin/python -m pytest
  scripts/test_agora_compat_manifest.py -q` completed with `16 passed`.
- `/home/lupin/pantheon/.venv/bin/python
  scripts/agora_compat_manifest.py verify --manifest
  docs/contracts/agora/dev-compatibility-manifest.json
  --backend-runtime-commit
  00b38f41ec51296762d502c4bd5732f95ccf2953
  --frontend-runtime-commit
  e4399e3ec68f882ace35d0349e6597cdd101525f
  --frontend-root /home/lupin/code/execute-plans
  --backend-dev-ref origin/dev --frontend-dev-ref origin/dev` returned `ok`.
- `sha256sum docs/contracts/agora/dev-compatibility-manifest.json` remained
  `494980f204f0af21effc018ebbba657c1027b3052e984577833dfa46ab360bb3`.
- Pantheon PR #4016 and closeout-evidence PR #4020 are merged into `dev` at
  `e2f7e7356b517844a946b780b373492d98af8c30` and
  `dd335e22809ee85ec9cc9a385fc30f6abae3197a`, respectively.

## Exclusions

- No arbitrary latest-`dev` deployment.
- No bypass or `--allow-pending` in the accepting deployment path.

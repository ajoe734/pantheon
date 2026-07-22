# AG-COMPAT-002-GATE — Finalize and enforce the Agora cross-repository compatibility gate

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Claude
Reviewer: Codex
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

## Exclusions

- No arbitrary latest-`dev` deployment.
- No bypass or `--allow-pending` in the accepting deployment path.

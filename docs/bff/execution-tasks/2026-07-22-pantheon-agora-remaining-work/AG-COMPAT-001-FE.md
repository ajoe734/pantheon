# AG-COMPAT-001-FE — Generate Agora frontend types and bind runtime identity

Priority: P1
Repository: `ajoe734/execute-plans`
Merge target: `dev`
Owner: Codex2
Reviewer: Codex
Depends on: `AG-PERF-TRUTH-001-FE`, `AG-CAND-TRUTH-001-FE`, `AG-COMPAT-001-BE`

## Objective

Generate and consume frontend types from the exact Agora backend contract
bundle, then emit the non-placeholder frontend evidence required by the dev
compatibility manifest.

## Owned scope

- generated Agora API types/client inputs in execute-plans
- type-generation/check scripts and CI gate
- performance/candidate/workshop client compile fixes required by generation
- task-scoped compatibility evidence

## Required work

1. Generate types from the backend contract commit named by
   `AG-COMPAT-001-BE`; do not hand-copy interfaces.
2. Make CI fail when committed generated output differs from regeneration.
3. Record frontend runtime commit, generated-from contract commit, bundle
   digests, OpenAPI digest, and deterministic generated-types digest.
4. Compile and test every Agora client using the new operations.

## Acceptance

- All manifest identity/hash fields supplied by this task are non-zero and
  reproducible from a clean checkout.
- Type generation is deterministic and enforced in CI.
- Production build, Agora tests, and strict BFF-client tests pass.
- PR merges to execute-plans `dev` and emits a machine-readable handoff for
  `AG-COMPAT-002-GATE`.

## Exclusions

- No Pantheon source copied into execute-plans.
- No manual editing of generated files without generator changes.

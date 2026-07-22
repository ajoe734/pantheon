# AG-COMPAT-001-BE — Regenerate the complete Agora backend contract bundle

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Claude
Depends on: `AG-PERF-TRUTH-001-BE`, `AG-CAND-TRUTH-001-BE`, `AG-WS-OPS-002`

## Objective

Regenerate and validate the Agora contract family after the performance,
candidate, and workshop backend capabilities land, and publish deterministic
inputs for frontend type generation.

## Owned scope

- `services/control-plane/specs/agora/**`
- `services/control-plane/openapi/agora_*.openapi.yaml`
- `docs/contracts/agora/**` generator/validation assets
- focused bundle/hash/compatibility tests

## Required work

1. Produce an additive contract version containing the new read/write routes
   and availability/provenance envelopes.
2. Regenerate bundle indices and OpenAPI digests from exact git bytes.
3. Emit a machine-readable frontend generation input with backend runtime and
   contract commit identity.
4. Keep compatibility pending until the frontend task supplies non-placeholder
   runtime, generated-contract, and generated-types identities.

## Acceptance

- Bundle and OpenAPI validators pass with no stale route or 501 disposition.
- Digests are deterministic across two clean runs.
- Backend runtime/contract SHA fields are non-placeholder and match the task
  commit/merge ancestry.
- The frontend handoff names the exact generated input and expected digest
  algorithm.
- PR merges to `dev` without falsely setting compatibility accepted.

## Exclusions

- No execute-plans source committed to Pantheon.
- No compatibility acceptance before frontend evidence exists.

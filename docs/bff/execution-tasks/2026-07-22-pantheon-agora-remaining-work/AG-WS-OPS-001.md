# AG-WS-OPS-001 — Durable Workshop versions and selected-version operations

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Status: `review_approved`
Owner: Codex2
Reviewer: Claude
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

## Objective

Implement the first three operations formally deferred by `AG-GAP-005`:

- `GET /bff/agora/workshops/{workshop_id}/versions`
- `POST /bff/agora/workshops/{workshop_id}/versions`
- `POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select`

## Owned scope

- `services/control-plane/bff/agora/strategy_workshop/**` version store/domain
  model and route handlers
- additive Agora contract/OpenAPI changes
- migration/backfill and focused durability/concurrency/authz tests

## Required work

Implement a durable, tenant-scoped StrategySpec version projection with stable
identity, parent lineage, immutable content digest, creator/time, selected
version, and ETag. Creation requires idempotency. Selection requires ownership
validation and atomic CAS. Existing workshop records need a deterministic
backfill/current-version rule.

## Acceptance

- All three routes return non-501 typed responses for authorized actors.
- Duplicate create idempotency keys return the same version.
- Stale ETag selection fails with a typed conflict and changes nothing.
- Cross-user/tenant version reads or selection are denied.
- Version rows and selected version survive BFF restart.
- Legacy workshop fixtures migrate without changing accepted payload bytes
  outside the additive fields.
- Focused tests and OpenAPI validation pass; PR merges to `dev`.

## Exclusions

- No research, consultation, or conclusion implementation; those belong to
  `AG-WS-OPS-002`.
- No order routing or live-capital effect.

## Review-approved delivery evidence

- Implementation anchor `7655572be` adds the durable version projection,
  immutable Strategy Registry content digest, deterministic legacy backfill,
  tenant isolation, selected pointer persistence, and route/store coverage.
- Contract anchor `a7959bbac` adds the hash-locked v1.10 bundle, capability
  manifest, typed resources, and OpenAPI for list/create/select without
  changing frozen v1.2/v1.8/v1.9 contract bytes.
- Owner verification passed 120 tests with 2 opt-in Postgres skips; the focused
  Postgres restart suite passed 4 tests and left zero isolated test schemas.
- Claude independently reran both suites and approved digest write-once,
  deterministic ETag-stable backfill, stale-CAS no-mutation, pre-Registry
  tenant isolation, restart persistence, and the additive v1.10 hash chain.
- Final closure still requires the task PR to merge into `dev` and the owner to
  run the governed `done` transition; `review_approved` alone is not terminal.

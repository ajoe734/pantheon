# AG-WS-OPS-001 — Durable Workshop versions and selected-version operations

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Claude
Reviewer: Antigravity
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

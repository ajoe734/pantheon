# PINT-014 — Daily candidate decision semantics

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependencies

- Repository: `ajoe734/pantheon`
- Base/merge target: latest `origin/dev`
- Hard dependencies: merged `PINT-011` and `PINT-013`

## Owned scope

- Create candidates only from exact persisted Persona `recommended_measures`.
- Durable modify/accept-for-review/reject/defer/cancel records with exact
  interaction, measure, actor, revision, digest, rationale, time, and audit.
- Server-authoritative validation adapter/receipt and distinct canonical formal
  approval linkage with expiry and self-approval denial.

## Acceptance

- Human topic text cannot become a fabricated candidate measure.
- Modify creates an immutable revision; accept-for-review is non-approval and
  has no order/broker/capital/binding/promotion/policy/memory side effect.
- Browser-supplied validation results are rejected; stale/revised/revoked,
  cross-tenant, digest, ETag/idempotency, expiry, and self-approval cases fail.
- Restart/readback and authority-negative tests pass; clean branch, scoped
  commit, PR, checks, distinct review, and merge.

## Excluded

No frontend, auth/session, release/deploy profile, or hosted acceptance.

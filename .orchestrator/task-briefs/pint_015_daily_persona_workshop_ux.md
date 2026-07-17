# PINT-015 — Daily Workshop and contextual Persona UX

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependencies

- Repository: `ajoe734/execute-plans` only; never materialize it under Pantheon
- Base/merge target: latest frontend delivery branch
- Hard dependencies: merged `PINT-011` through `PINT-014`

## Owned scope

- Persona Detail Talk/Challenge/Compare/Propose/Reflect entry actions and one
  canonical context-preserving Workshop from Trading Room, Journal, and Inbox.
- Backend-truth lifecycle/timeline, independent opinions, disagreement,
  synthesis, provider failure, candidate modify/accept/reject/defer,
  validation/reviewer, durable readback, responsive and accessible states.

## Acceptance

- No fixed demo context, simulator toggle, direct-API fallback, browser write
  override, or invented success state in the live path.
- Viewer fails closed; operator completes the governed UI flow and reload keeps
  exact linkage/provenance.
- Desktop/mobile unit/integration/strict-live tests pass; clean frontend
  worktree, scoped commit/trailers, push, PR, checks, distinct review, merge.

## Excluded

No Pantheon source, production trading/capital authority, auth issuer, or
release/deployment controller changes.

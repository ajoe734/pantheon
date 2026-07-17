# PINT-013 — Durable interaction lifecycle and readback

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependency

- Repository: `ajoe734/pantheon`
- Base/merge target: latest `origin/dev`
- Hard dependency: merged `PINT-012`

## Owned scope

- Postgres-owned requests, invocations, opinions, synthesis, failures, outbox,
  candidate links, and audit provenance.
- Tenant-scoped interaction list/detail readback plus Workshop timeline/SSE
  projection.
- Idempotent claim/retry, reconnect replay, pending outbox drain, and BFF
  restart recovery with RPO zero.

## Acceptance

- Reload, relogin, SSE reconnect, duplicate request, and BFF restart return the
  same authoritative records without duplicate provider side effects/cards.
- Partial failure retains successful opinions and exact missing/degraded
  participants.
- Process-local maps, SSE buffers, cards, browser state, and provider history
  are not authoritative stores.
- Focused persistence/route/recovery and adjacent Agora tests pass; clean
  branch/PR/check/review/merge workflow is mandatory.

## Excluded

No frontend work, browser auth, release/deploy profile, or hosted closeout.

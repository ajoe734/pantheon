# PINT-017 — Persistent operator-live release profile

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependencies

- Repository: `ajoe734/execute-plans` only
- Base/merge target: latest frontend delivery branch
- Hard dependencies: merged `PINT-015` and `PINT-016`

## Owned scope

- Independent immutable `operator-live` artifact/profile, digest/identity,
  integration gate, deployment controller/workflow, rollback policy, manifest,
  tests, and runbook.
- Exact healthy strict BFF SHA gate with live/strict, real writes true, stub
  writes false, and no embedded bearer.

## Acceptance

- `read-only`, `operator-live`, and bounded `write-proof` remain separate exact
  artifacts/digests.
- Operator-live does not arm proof watchdog or auto-restore read-only; rollback
  selects an exact accepted operator/read-only artifact.
- Bundle scan proves zero credential material; deployment and integration tests
  pass through clean frontend branch, PR, checks, review, and merge.

## Excluded

No global workflow disable/cancel, permissive stub, browser storage override,
Pantheon source, or production trading/capital authority.

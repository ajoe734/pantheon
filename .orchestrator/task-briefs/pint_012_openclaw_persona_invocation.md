# PINT-012 — Real selected-Persona OpenClaw invocation

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependency

- Repository: `ajoe734/pantheon`
- Base/merge target: latest `origin/dev`
- Hard dependency: merged `PINT-011`

## Owned scope

- Governed Persona agent admission/ensure and one authenticated OpenClaw
  invocation per frozen selected Persona/version/capability snapshot.
- Validate typed provider output and persist exact request/response correlation
  provenance before synthesis.
- Preserve successful independent opinions during partial failure and represent
  provider outage as degraded/failed without a forged memo.
- Remove `simulate_interaction_debate_and_synthesis` and every production
  keyword/magic-topic behavior.

## Acceptance

- Tenant, Persona/version, provider agent, workspace, environment ceiling, and
  capability snapshot fail closed.
- Unique provider output is visible in authoritative readback; synthesis cannot
  overwrite individual opinions.
- Tests prove zero tool/order/broker/capital/binding/promotion/policy/memory
  authority and no fake opinion on outage.
- Clean worktree, focused/adjacent tests, scoped trailers, push, PR, visible
  checks, distinct review, and merge.

## Excluded

No frontend, browser auth, release profile, deployment, or formal candidate
decision UI.

# Consensus Packet

## Decision Summary

- Session: `phase6-2026-05-01-pantheon-p0-paper-loop`
- Scope: turn `docs/04` SA and P0 SD findings into the next execution wave for Pantheon P0.
- Accepted architecture:
  - Pantheon is a governed operating system, not a single trading model or UI shell.
  - P0 execution target is `pantheon/lean` submodule / `ajoe734/pantheon-lean.git`.
  - `lean-platform` is not-current-runtime unless an ADR marks a migration-only path.
  - P0 proves paper-only runtime; live/canary remain fail-closed and out of activation scope.
  - Runtime identity pivots on `RuntimeBinding`.
  - BFF/front display and command surfaces must not become canonical truth.
- Delivery order:
  1. repo authority and CI guardrails
  2. runtime bootstrap/context contract
  3. paper telemetry producer and ingest/projection
  4. paper operating loop smoke and basic reconciliation
  5. frontend/BFF production honesty cleanup

## Agreed Task Slices

- `P0-EXEC-ADR-001`: land bridge ADR and repo mapping.
- `P0-CI-BRIDGE-001`: submodule authority and no-wrong-repo CI.
- `P0-BOOT-001`: materialize `RuntimeBootstrapRequest`.
- `P0-CTX-001` / `P0-CTX-002`: runtime context model and `runtime_bootstrap.py` wiring.
- `P0-LEAN-CTX-001`: `PantheonAlgoBase` context/event attachment.
- `P0-TEL-001` / `P0-TEL-PROJ-001`: paper telemetry and runtime projection.
- `P0-LOOP-001` / `P0-REC-001`: paper loop smoke and basic reconciliation.
- `P0-STATE-001`: state machine invariant tests.
- `P0-BFF-CMD-001`: BFF read/command split.
- `P0-FE-DEMO-001` / `P0-FE-SOURCE-001`: frontend demo cutoff, source mode, runtime identity.
- `P0-LIVE-GUARD-001`: live fail-closed and bracket honesty.
- `P0-CI-BOUNDED-001`: source/search bounded and fail-closed adapter CI.
- `P0-HEALTH-001`: health endpoint cleanup scan.

## Open Questions / Human Gate

- Should BFF read/command split be strict P0 or allowed as first P1? Starter recommendation: keep in P0 because runtime-affecting commands need idempotency/audit before paper-loop proof is trusted.
- Should basic reconciliation materialize in the first wave? Starter recommendation: yes, but only as paper baseline `ReconciliationRecord`; no automatic evolution action.
- Should `lean-platform` be archived immediately or kept as migration candidate? Starter recommendation: migration candidate only until ADR revision.

## Acceptance Note

- Waiting for lane readouts and human acceptance.

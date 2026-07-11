# MGMT-OPS-003-GAP-002 - Runtime Binding And Telemetry Truth

Owner: Copilot

Reviewer: Codex2

Repository: `ajoe734/pantheon`

Merge target: `dev`

## Goal

Repair or explicitly quarantine the runtime identity and telemetry gaps exposed
by the live Portfolio Book response. This task must improve source truth rather
than suppress diagnostics.

## Required Work

- Trace each missing persona-capital binding to its runtime, deployment plan,
  capital pool, artifact, and bootstrap/reconciliation path.
- Restore valid bindings where authoritative identifiers exist. Records that
  cannot be repaired must remain visible and be explicitly quarantined with an
  auditable reason.
- Normalize broker, paper-ledger, canary-sleeve, and live-capital-pool identity
  propagation from runtime creation through telemetry and Portfolio Book.
- Restore telemetry coverage for active runtimes or publish an explicit source
  status and incident for every uncovered runtime.
- Ensure downstream attribution remains partial, degraded, or unavailable
  until required identity and telemetry joins are trustworthy.

## Acceptance

- A reconciliation report accounts for every hosted missing-binding and
  telemetry gap; no row disappears to make counters look healthy.
- Active runtime binding, telemetry, and Portfolio Book contract tests cover
  normal, missing, stale, quarantined, and repaired paths.
- Re-running reconciliation is idempotent and produces an audit trail.
- Hosted BFF evidence records before/after counts for runtimes, telemetry
  runtimes, degraded rows, missing bindings, broker identity, and capital scope.
- Formal attribution is impossible for rows whose required joins remain
  degraded or unavailable.
- Pantheon PR, checks, merge SHA, BFF deploy run, and authenticated live probes
  are recorded.
- Reviewer independently samples raw runtime/binding/telemetry sources and
  completes `REVIEWER_CHECKLIST.md`; summary-counter-only review is forbidden.

## Artifacts

- `services/control-plane/bff`
- `services/runtime-manager`
- `services/persona`
- `services/telemetry`
- `scripts`
- `docs/deployment/evidence`

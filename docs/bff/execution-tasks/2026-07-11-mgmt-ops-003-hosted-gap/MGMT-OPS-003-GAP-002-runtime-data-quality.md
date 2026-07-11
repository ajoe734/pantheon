# MGMT-OPS-003-GAP-002 - Runtime Binding And Telemetry Truth

Owner: Codex

Reviewer: Claude

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

## Implementation Checkpoint

The task branch now contains a read-only, fail-closed reconciliation path in
`services/runtime-manager/runtime_truth_reconciler.py` and
`services/runtime-manager/reconcile_runtime_truth.py`.

- Runtime bindings are the driving rows, so missing plan, persona binding,
  capital-pool, or telemetry joins remain visible in the report.
- Identity fields are proposed for repair only when the runtime, deployment
  plan, persona-capital binding, and telemetry sources agree. The reconciler
  does not write another service's store.
- Conflicting or insufficient evidence produces an explicit quarantine record
  with issue codes and evidence references.
- A repair proposal remains ineligible for formal attribution until the
  authoritative write owner applies it and a fresh source capture reconciles
  without issues.
- The snapshot hash is the idempotency key and the append-only audit contains
  one entry per distinct snapshot.

Focused verification on 2026-07-11:

```text
pytest -q services/runtime-manager/test_runtime_truth_reconciler.py
7 passed

pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py
46 passed, 4 deprecation warnings
```

## Hosted Evidence Boundary

Authenticated before/after counts, deployed SHA ancestry, and independent raw
source sampling are intentionally not claimed by this implementation
checkpoint. They must be captured after the Pantheon PR merges and that merge
SHA is deployed to the dev BFF. Until then, unresolved hosted rows remain
quarantined and formal attribution remains fail-closed.

## Hosted Evidence Checkpoint (2026-07-11)

Dev BFF deploy run `29151498421` successfully deployed
`636f989563157c78118de17b81ef8651389a7acd`, which contains implementation
merge `18d064477a5ec88740b7da4b879735be589df97e` by ancestry. Authenticated API
and desktop/mobile browser captures are recorded under
`docs/deployment/evidence/mgmt-ops-003-gap/gap-002/20260711T114815Z/`.

The remaining verdict is `REQUEST_CHANGES`, now for a current UI-to-API
difference rather than deployment freshness. The BFF reports 10 runtimes, 5
telemetry runtimes, and keeps all 10 missing-binding holdings visible among 18
degraded incident-backed rows. The hosted UI reports `Telemetry Runtime 0` and
labels some `0/0` pool rows as covered. This task must remain fail-closed until
the frontend incident/count/confidence work in `MGMT-OPS-003-GAP-001` is
deployed and independently sampled.

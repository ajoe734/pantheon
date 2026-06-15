# E2E Business-Flow Verification Campaign — Master Index (R1–R10)

A 10-round campaign verifying Pantheon's complete business flows end-to-end
against the **deployed dev stack**, each round shipping a CI-gated verification
program, fixing what is a code bug, and flagging upstream data/build gaps.

## How to run the live checks

```
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
BFF_TOKEN=op-dev:admin:mfa \
  scripts/run_e2e_verifiers.sh
```

Runs all BFF-based e2e verifiers and summarizes pass/fail. The per-verifier unit
tests run in CI via `run-acceptance.sh` full mode (`e2e-*-verifier` steps).

## Rounds

| # | Flow | Verifier | Live finding | PR |
|---|------|----------|--------------|----|
| R1 | strategy→artifact→plan→pool→binding | `verify_e2e_binding_provenance.py` | 34 dangling provenance refs (strategies 404, artifact read-model unavailable) | #1609 |
| R2 | fill→telemetry→reconcile→drift | `verify_e2e_telemetry_drift_consistency.py` | 15/15 runtimes: telemetry has trades, drift observed_state=0 (disconnect) | #1612 |
| R3 | plan→approval→promotion | `verify_e2e_promotion_governance.py` | 15/15 plans `approved` against non-existent approvals (phantom auth) | #1614 |
| R4 | pause/kill-switch→execution halt | **code fix** in `paper_runtime.py` + tests | paused binding kept filling orders — **fixed** (drain halts on non-active status) | #1618 |
| R5 | pool→binding capital | `verify_e2e_capital_integrity.py` | invariants HOLD (no over-allocation); 1 orphan devloop pool | #1619 |
| R6 | signal→consume idempotency | dedup regression test | double-fill prevention HOLDS (same+cross batch); in-memory dedup restart limitation flagged | #1621 |
| R7 | telemetry ingest validation/DLQ | `verify_e2e_telemetry_dlq_health.py` | ingest rejects unknown bindings (holds); DLQ pinned at threshold (99 unreplayable) | #1622 |
| R8 | operator read-surface consistency | `verify_e2e_surface_consistency.py` | runtime ids consistent; persona-health disconnected from active fleet (4 missing) | #1623 |
| R9 | incident→evolution→artifact | `verify_e2e_evolution_loop.py` | 3 malformed open incidents (untitled/no runtime), 0 evolution programs | #1624 |
| R10 | consolidation | `run_e2e_verifiers.sh` + this index | one-command live e2e health check | — |

## Cross-cutting conclusions

1. **The execution + telemetry path is genuinely live** (proved in V11 + R2: real
   fills, positions, pnl flow through telemetry). The right-half data path works.
2. **The surrounding surfaces are served from static rescue-placeholder stores
   disconnected from the live fleet.** Provenance (R1), approvals (R3), drift
   (R2), persona-health (R8), and evolution/incidents (R9) all read curated or
   rescue data rather than the active runtime reality. This is the dominant
   systemic gap — a data/build concern, repeatedly confirmed, deliberately not
   faked to make checks pass (faking would forge audit trails / hide the gap).
3. **One real safety bug was found and fixed (R4):** the paper execution loop
   ignored binding pause / kill-switch and kept filling orders; `drain_once` now
   halts on non-active binding status.
4. **Two safety properties were proven to hold:** ingest binding-validation (R7)
   and signal idempotency (R6) — both gated against regression, with their
   limitations (stuck DLQ, in-memory dedup) flagged.

## Standing follow-ups (upstream build, not faked here)

- Materialize real strategy / artifact / approval / capital records for active
  bindings, or retire the rescue placeholders (R1/R3/R5).
- Wire the live reconciliation-drift computation into the operator drift surface
  (R2) and the active-fleet personas into persona-health (R8).
- Acknowledge/purge permanently-unreplayable binding-mismatch DLQ entries (R7).
- Persist signal dedup across worker restarts (R6); produce well-formed incidents
  that drive the evolution arc (R9).

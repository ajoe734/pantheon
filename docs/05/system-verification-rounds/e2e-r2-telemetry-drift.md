# E2E-R2 — Telemetry → reconciliation → paper-live-drift consistency

**Round:** E2E-R2 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r2-telemetry-reconcile
**Business flow:** paper fill → telemetry ingest → runtime_summary projection →
reconciliation/drift → operator paper-live-drift view.

## Plan

1. Drive real paper fills (done in V11 — telemetry now holds live executions).
2. Cross-check each runtime's telemetry runtime-summary against the operator
   paper-live-drift `observed_state`.
3. Ship the cross-check as a CI-gated verifier; fix or precisely flag the gap.

## Verification program

`scripts/verify_e2e_telemetry_drift_consistency.py` (+ unit test), wired into
`run-acceptance.sh` full mode as `e2e-telemetry-drift-verifier`. FAILs when a
runtime's telemetry shows `total_trades > 0` while its drift `observed_state`
reports zero — a disconnect between the live data path and the operator drift
surface.

## Live result (dev, 2026-06-15)

```
telemetry vs paper-live-drift over 16 runtimes:
  15 runtimes have real telemetry trades (pnl -14.03 .. +61.23)
  ALL 15 paper-live-drift observed_state report total_trades=0, pnl=0
  -> 15/15 DISCONNECTS
```

Example: `rt-rescue-0260531-1715d8d2` — telemetry runtime-summary shows 6 trades,
pnl +61.23, positions `{MSFT: 6, NVDA: 4}`; the operator paper-live-drift
`observed_state` shows `total_trades: 0, pnl: 0, source: paper_runtime_rescue_observed_state`.

## Root cause (traced through code)

- **Telemetry is correct.** `/api/v1/telemetry/{rt}/summary` reflects executed
  paper fills (real positions + pnl) — the V11-driven trades flow through.
- **The drift computer exists and is real.** `services/reconciliation-drift/main.py`
  computes `_observed_metrics()` from telemetry events and runs `_drift_checks()`
  against thresholds — genuine drift logic.
- **But the operator drift view is disconnected.**
  `/api/v1/operator/paper-live-drift/{id}` (BFF) serves
  `read_store.get_paper_live_drift_report()` from the static
  `paper_live_drift_reports` store (`/data/bff/paper_live_drift_reports.json`),
  whose `observed_state` is a `paper_runtime_rescue_observed_state` stub with
  `total_trades: 0`. The BFF passes the stored report through verbatim — by
  contract: `test_pkt014_paper_live_drift_returns_backend_owned_comparison_payload`
  asserts the payload is **backend-owned**. So the BFF is behaving correctly; the
  gap is that **nothing wires the reconciliation-drift computation (or telemetry
  runtime_summary) into the `paper_live_drift_reports` store the BFF reads.**

## Disposition

- **Shipped (code/CI):** the telemetry↔drift consistency verifier + logic test +
  CI gate, so this disconnect is caught going forward (currently FAILs against
  dev — reporting the real gap).
- **Flagged (backend integration, not hacked here):** populate the backend-owned
  `paper_live_drift_reports` from the live drift computation /
  telemetry runtime_summary (the reconciliation-drift service output). A BFF-side
  override of `observed_state` was deliberately NOT done — it would violate the
  backend-owned contract (test_pkt014) and mask the real integration gap.

## Next round

E2E-R3: governance promotion flow (paper → canary → live readiness gates,
PromotionReadinessPacket / EP5 proof), or deepen R2 by also reconciling
`drift_groups` status + positions, not just trade counts.

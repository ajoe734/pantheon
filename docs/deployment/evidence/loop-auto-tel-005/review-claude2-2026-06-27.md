# Review: LOOP-AUTO-TEL-005 — Add telemetry incident replay and operator evidence

Reviewer: Claude2
Date: 2026-06-27
Decision: **APPROVED**

## Scope reviewed

- `docs/deployment/evidence/loop-auto-tel-005/README.md` — evidence doc
- `scripts/replay_telemetry_incidents.py` — live-service operator replay script
- `services/incidents/fixtures/order_rejection_spike_telemetry.json`
- `services/incidents/fixtures/heartbeat_loss_telemetry.json`
- `services/incidents/tests/test_incident_replay_suite.py` (17 tests)
- `services/reconciliation-drift/fixtures/pnl_drift_telemetry_event.json`
- `services/reconciliation-drift/fixtures/recovery_telemetry_event.json`
- `services/reconciliation-drift/tests/test_tel005_replay_suite.py` (8 tests)

## Acceptance criteria — all met

| Criterion | Evidence | Verdict |
|---|---|---|
| Replay proves order rejection spike opens incident | `TestOrderRejectionSpikeReplay::test_replay_opens_incident` — POST `/api/incidents/consume-threshold` returns 201, `incident_id=inc-tel005-order-rejection-spike-001`, `status=open` | ✅ |
| Replay proves heartbeat loss opens incident | `TestHeartbeatLossReplay::test_replay_opens_incident` — same pattern, `incident_id=inc-tel005-heartbeat-loss-001`, `heartbeat_lag_ms` in evidence_summary | ✅ |
| BFF incident and runtime panels agree on authoritative projection | `test_operator_payload_agrees_with_incident` for both spike scenarios — GET `/api/incidents/{id}/operator-payload` returns identical `incident_id`, `status`, `severity`, `binding_id`, `telemetry_event_ids`, and `is_open=true` as the underlying incident record | ✅ |

## Additional proofs

- **Idempotency**: Both spike scenarios tested — second `consume-threshold` returns 200 (not 201); store count stays at 1. PnL drift idempotency also tested at HTTP layer (same drift_report_id, list returns 1 record).
- **PnL drift → DriftReport → incident**: Verified at both consumer-function level and HTTP endpoint level; `consume-drift-report` creates incident with correct `binding_id`, `runtime_id`, `telemetry_event_ids`.
- **Recovery path**: Recovery event (all metrics within threshold) produces `drift_report_count=0`; event appears in `ignored_event_ids`; no incident created. Both consumer-level and HTTP-level tested.
- **Cross-scenario isolation**: Both spike incidents coexist; `?open_only=true` returns both; `?binding_id=` filter works; operator payloads are independent.
- **Evidence linkage fields**: `drift_report_id`, `recon_run_id`, `incident_cluster_id`, `evidence_refs` all verified to be populated.

## Non-goals compliance

- No live-capital behavior changed ✅
- No approval gate bypass ✅
- No panel-only closure ✅ — evidence is in executed test code
- No seed fixture as live proof ✅ — fixtures exercised through test code with assertions

## Dispatch rule compliance

- Idempotent duplicate ticks and events: proven ✅
- Operator-visible truth projection required: proven via operator-payload endpoint assertions ✅
- Maturity cannot rise above collected evidence: 25 tests passed ✅

## Validation summary

Per commit trailers and evidence doc:
- `pytest services/incidents/tests/test_incident_replay_suite.py services/reconciliation-drift/tests/test_tel005_replay_suite.py` → 25 passed
- Full suite: `pytest services/incidents/test_main_routes.py services/incidents/tests/test_incident_replay_suite.py services/reconciliation-drift/tests/` → 87 passed (no regressions)

## Notes

The commit trailers list `Reviewer: Codex` (original assignment) — review was auto-reassigned to Claude2 due to Codex2 usage-limit terminal. The implementation quality is correct regardless of reviewer reassignment.

The replay script (`scripts/replay_telemetry_incidents.py`) is a live-service operator tool; it is not counted as test evidence by itself, but it provides a clear runbook for human verification against a running stack.

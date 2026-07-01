# LOOP-AUTO-TEL-005: Telemetry Incident Replay and Operator Evidence

Task: LOOP-AUTO-TEL-005 — Add telemetry incident replay and operator evidence
Owner: Claude
Reviewer: Codex
Date: 2026-06-27

## Delivered Behavior

This task adds a complete replay suite and operator evidence for four telemetry
incident scenarios that feed the Wave 4 Telemetry Reconciliation loop:

| Scenario | Fixture | Path |
|---|---|---|
| Order rejection spike | `order_rejection_spike_telemetry.json` | `services/incidents/fixtures/` |
| Heartbeat loss | `heartbeat_loss_telemetry.json` | `services/incidents/fixtures/` |
| PnL drift (reconciliation path) | `pnl_drift_telemetry_event.json` | `services/reconciliation-drift/fixtures/` |
| Recovery (no drift) | `recovery_telemetry_event.json` | `services/reconciliation-drift/fixtures/` |

### Tests added

| Module | Tests | What is proven |
|---|---|---|
| `services/incidents/tests/test_incident_replay_suite.py` | 17 | All four scenarios via the incident service |
| `services/reconciliation-drift/tests/test_tel005_replay_suite.py` | 8 | PnL drift and recovery via the reconciliation-drift service |

### Replay script

`scripts/replay_telemetry_incidents.py` drives all four scenarios against
live services and prints a JSON evidence summary. Usage:

```bash
python3 scripts/replay_telemetry_incidents.py \
    --incidents-url http://localhost:8090 \
    --drift-url http://localhost:8102 \
    --json-output /tmp/tel005-evidence.json
```

## Acceptance Mapping

| Acceptance criterion | Evidence |
|---|---|
| Replay proves order rejection spike opens incident | `TestOrderRejectionSpikeReplay::test_replay_opens_incident` — `POST /api/incidents/consume-threshold` returns 201 with `incident_id=inc-tel005-order-rejection-spike-001`, `status=open`, `telemetry_event_ids` populated |
| Replay proves heartbeat loss opens incident | `TestHeartbeatLossReplay::test_replay_opens_incident` — same pattern, `incident_id=inc-tel005-heartbeat-loss-001`, `heartbeat_lag_ms` in evidence_summary |
| BFF incident and runtime panels agree on authoritative projection | `test_operator_payload_agrees_with_incident` for both scenarios — `GET /api/incidents/{id}/operator-payload` returns identical `incident_id`, `status`, `severity`, `binding_id`, `telemetry_event_ids`, and `is_open=true` as the underlying incident record |

Additional proofs:

- **Idempotent replay**: second `consume-threshold` call returns HTTP 200 (not 201); store count stays at 1.
- **PnL drift → incident**: `pnl_drift_telemetry_event.json` fed through `reconciliation-drift` produces a `DriftReport`; that report forwarded to `POST /api/incidents/consume-drift-report` creates an `IncidentCase` with correct `binding_id`, `runtime_id`, and `telemetry_event_ids`.
- **Recovery path**: `recovery_telemetry_event.json` (all metrics within threshold) produces `drift_report_count=0` and the event_id appears in `ignored_event_ids`; no incident is created.
- **Cross-scenario projection**: both spike incidents coexist; `GET /api/incidents?open_only=true` returns both; `binding_id` filter returns both; operator payloads are independent.

## Validation Run

```
python3 -m pytest services/incidents/tests/test_incident_replay_suite.py \
    services/reconciliation-drift/tests/test_tel005_replay_suite.py -v
# 25 passed
```

Full suite (including pre-existing tests):

```
python3 -m pytest services/incidents/test_main_routes.py \
    services/incidents/tests/test_incident_replay_suite.py \
    services/reconciliation-drift/tests/ -q
# 87 passed
```

No live-capital behavior changed. The replay suite operates against the
same telemetry-ingest → reconciliation-drift → incident service pathway
that Wave 4 depends on. No seed fixture is counted as live proof; all
acceptance criteria are satisfied by executed test code.

# LOOP-AUTO-TEL-002: Scheduled Reconciliation Worker — Evidence

Task: LOOP-AUTO-TEL-002 — Add scheduled reconciliation worker
Owner: Codex (helper-claimed re-dispatch)
Reviewer: Claude
Date: 2026-07-01

Original implementation PR: <https://github.com/ajoe734/pantheon/pull/2426>
- Merged: 2026-06-27T14:22:05Z
- Merge commit: `d2a02f08bb3b821b2dbb6f0753c5c83ba226aa98`
- Final task-branch head: `b45d5712d01cb005838302f7676d856fc0335cbd`

## Deliverables

### 1. `services/reconciliation-drift/scheduler_worker.py`

New standalone scheduler process. On each tick:
- POSTs to `POST /api/reconciliation-drift/scheduled-reconcile`
- Controlled by `RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS` (default 300s)
- Controlled by `RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS` (default 0 = unlimited)
- Prints JSON tick summary to stdout

### 2. `POST /api/reconciliation-drift/scheduled-reconcile` endpoint (main.py)

Added to the reconciliation-drift FastAPI service. Per tick:
- Accepts optional `tick_id` (generated from UTC timestamp if absent)
- Fetches runtime summaries from `PANTHEON_TELEMETRY_API_URL/api/telemetry/runtime-summaries`
- For each summary with a `binding_id`, creates an evaluation record linking:
  - `binding_id` (telemetry binding identifier)
  - `runtime_id` (runtime session identifier)
  - `telemetry_event_ids` (telemetry evidence links)
- **Idempotent**: evaluation_id is deterministic from `tick_id + binding_id`.
  A second call with the same `tick_id` skips already-evaluated bindings,
  returning them in `skipped_binding_ids`.

Response shape:
```json
{
  "status": "ok",
  "tick_id": "2026-06-27T13:45:00Z",
  "trigger": "scheduled",
  "evaluated_binding_count": 3,
  "skipped_binding_count": 0,
  "evaluation_ids": ["rdeval-sched-...", ...],
  "skipped_binding_ids": [],
  "telemetry_summaries_fetched": 3,
  "triggered_at": "2026-06-27T13:45:00Z"
}
```

### 3. `docker-compose.yml` — `reconciliation-drift-scheduler` service

New service under profile `reconciliation-drift-scheduler`:
```yaml
reconciliation-drift-scheduler:
  profiles: ["reconciliation-drift-scheduler"]
  command: ["python", "services/reconciliation-drift/scheduler_worker.py"]
  environment:
    RECONCILIATION_DRIFT_URL: http://reconciliation-drift-svc:8102
    RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS: ${..:-300}
    RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS: ${..:-0}
  depends_on:
    reconciliation-drift-svc:
      condition: service_healthy
```

## Test Evidence

Current Codex verification, 2026-07-01:

```
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py services/reconciliation-drift/tests/test_reconciliation_drift_compose_activation.py -q
8 passed in 3.34s

python3 -m pytest services/reconciliation-drift/tests -q
21 passed in 5.85s
```

Original implementation verification, 2026-06-27:

```
python3 -m pytest services/reconciliation-drift/tests/ -v
20 passed in 10.38s
```

Key tests added (`test_reconciliation_drift_scheduler.py`):
- `test_scheduled_reconcile_empty_telemetry` — zero evaluations when telemetry URL absent
- `test_scheduled_reconcile_with_telemetry_summaries` — 2 summaries → 2 evaluations with binding_id/runtime_id links
- `test_scheduled_reconcile_idempotent_same_tick_id` — second tick with same tick_id skips the already-evaluated binding (0 new, 1 skipped)
- `test_scheduled_reconcile_different_tick_ids_create_separate_records` — different tick_ids → 2 separate evaluation records for same binding

## Acceptance Verification

| Criterion | Status |
|---|---|
| Reconciliation runs from schedule without manual POST | ✅ scheduler_worker.py triggers via POST /scheduled-reconcile |
| Duplicate ticks do not duplicate reconciliation records | ✅ tick_id-based evaluation_id prevents duplicates |
| Worker links telemetry binding and runtime identifiers | ✅ evaluation links binding_id, runtime_id, telemetry_event_ids |

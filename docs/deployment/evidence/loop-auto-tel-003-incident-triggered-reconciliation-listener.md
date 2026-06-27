# LOOP-AUTO-TEL-003: Incident-Triggered Reconciliation Listener Evidence

Task: LOOP-AUTO-TEL-003 - Add incident-triggered reconciliation listener
Owner: Codex2
Reviewer: Claude2
Date: 2026-06-27

## Deliverables

### 1. Incident trigger endpoint

`POST /api/reconciliation-drift/incident-triggers/consume`

The reconciliation-drift service now consumes incident/anomaly payloads and
immediately creates a reconciliation evaluation with:

- `trigger=incident`
- deterministic `evaluation_id` from `(incident_id or source_event_id) + binding_id`
- `incident_id`, `source_event_id`, `telemetry_event_ids`, and `trigger_reason`
- source contract preserving `incidents` as incident truth owner and
  `telemetry-ingest` as telemetry truth owner

Duplicate incident or anomaly events return the existing evaluation as
`created=false` and `skipped=true`.

### 2. Incident listener worker

`services/reconciliation-drift/incident_listener.py`

On each tick the worker:

- reads open incidents from `GET /api/incidents?open_only=true`
- posts each incident to
  `POST /api/reconciliation-drift/incident-triggers/consume`
- prints a JSON tick summary including fetched, triggered, and error counts

This gives heartbeat-loss and order-rejection-spike IncidentCases an immediate
reconciliation trigger path without requiring an operator to call the scheduled
reconciliation endpoint.

### 3. Compose profile

`docker-compose.yml` now includes:

```yaml
reconciliation-drift-incident-listener:
  profiles: ["reconciliation-drift-incident-listener"]
  command: ["python", "services/reconciliation-drift/incident_listener.py"]
  environment:
    RECONCILIATION_DRIFT_URL: http://reconciliation-drift-svc:8102
    PANTHEON_INCIDENTS_API_URL: http://incidents:8090
```

The listener depends on healthy `reconciliation-drift-svc` and `incidents`.

## Acceptance Verification

| Criterion | Evidence |
|---|---|
| Runtime anomaly triggers reconciliation without manual POST | `incident_listener.run_tick()` reads open incidents and posts to the trigger endpoint; compose wires the listener as an opt-in worker profile. |
| Listener is idempotent for duplicate anomaly events | `evaluation_id` is deterministic from incident/source event plus binding; duplicate POST returns `created=false`, and only one evaluation remains. |
| Trigger path records source event and reason | Stored evaluation includes `incident_id`, `source_event_id`, `telemetry_event_ids`, `trigger_reason`, and an `incident_trigger_received` reconciliation check. |

## Validation

```bash
python3 -m pytest services/reconciliation-drift/tests/test_reconciliation_drift_incident_listener.py services/reconciliation-drift/tests/test_reconciliation_drift_scheduler.py services/reconciliation-drift/tests/test_reconciliation_drift_compose_activation.py -q
# 11 passed in 5.03s

python3 -m pytest services/reconciliation-drift/tests/ -q
# 24 passed in 11.97s

# Post-merge validation on latest origin/dev base:
python3 -m pytest services/reconciliation-drift/tests/ -q
# 24 passed in 25.87s

python3 -m pytest services/incidents/test_main_routes.py -q
# 27 passed in 8.75s
```

No live-capital behavior changed. The listener only creates derived
reconciliation evaluations.

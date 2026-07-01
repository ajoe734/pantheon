# LOOP-AUTO-TEL-004: DriftReport Incident Classification Evidence

Task: LOOP-AUTO-TEL-004 - Classify drift reports into incidents with dedupe
Owner: Codex2
Reviewer: Codex
Date: 2026-06-27

## Delivered Behavior

- `services/incidents` now accepts `POST /api/incidents/consume-drift-report`.
- A DriftReport threshold breach creates an `IncidentCase` through the incident
  service writer, not by direct reconciliation-store mutation.
- Duplicate DriftReports dedupe by `binding_id + runtime_id + incident_cluster_id`.
- Duplicate cluster evidence updates the existing open IncidentCase by merging:
  - `telemetry_event_ids`
  - `reconciliation_ids`
  - highest observed severity
- `IncidentCase` now carries optional `reconciliation_ids` and
  `incident_cluster_id` fields in the domain model, API models, JSON schema,
  and operator payload.
- `services/reconciliation-drift` now preserves incident-required evidence on
  DriftReports and calls the incident service's drift-report consume endpoint
  when `PANTHEON_INCIDENTS_API_URL` is configured.

## Acceptance Mapping

| Acceptance | Evidence |
|---|---|
| Threshold breach opens or updates one incident | `test_consume_drift_report_route_creates_incident_case` and `test_consume_drift_report_route_dedupes_by_binding_runtime_cluster` |
| Duplicate reconciliation does not duplicate incidents | Duplicate DriftReports with distinct `drift_report_id` and telemetry ids return HTTP 200 and leave `len(store.list_incidents()) == 1` |
| Incident links telemetry event ids, binding id, runtime id, and reconciliation ids | New IncidentResponse assertions cover `telemetry_event_ids`, `binding_id`, `runtime_id`, and `reconciliation_ids` |

## Validation

```bash
python3 -m pytest services/incidents/test_main_routes.py services/incident/test_incident_evidence_collector.py services/reconciliation-drift/tests/ -q
# 89 passed in 24.64s
```

No live-capital behavior changed. The reconciliation service only sends
DriftReport evidence to the incident writer when the incident service URL is
configured.

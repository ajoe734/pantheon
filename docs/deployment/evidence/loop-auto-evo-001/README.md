# LOOP-AUTO-EVO-001 Evidence

Task: Create postmortem drafts from resolved incidents

## Delivered Scope

- Added an idempotent resolved `IncidentCase` consumer in `services/postmortems`.
- Added `POST /api/postmortems/consume-resolved-incident`.
- Extended `Postmortem` with machine-readable incident evidence refs:
  `telemetry_event_ids`, `reconciliation_ids`, `incident_cluster_id`,
  `incident_evidence_summary`, and `lineage_ref`.
- Added draft-only store update semantics so duplicate resolved events refresh
  draft evidence without creating duplicate postmortems.

## Acceptance Mapping

- Resolved incident creates or updates a postmortem draft:
  covered by `test_consume_resolved_incident_creates_postmortem_draft` and
  `test_consume_resolved_incident_refreshes_existing_draft_evidence`.
- Draft links telemetry reconciliation and incident evidence:
  covered by copied telemetry, reconciliation, cluster, evidence summary, and
  lineage assertions in postmortem route tests.
- Duplicate resolved events do not create duplicate drafts:
  covered by `test_consume_resolved_incident_duplicate_event_is_idempotent`.

## Verification

```bash
python3 -m pytest services/incident/test_incident.py services/postmortems/test_main_routes.py
```

Result: `104 passed in 17.78s`.

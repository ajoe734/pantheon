# Task Brief: LOOP-AUTO-TEL-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Classify drift reports into incidents with dedupe
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Implemented drift-report incident classification; validation and PR closeout pending.

## Summary
把 drift report threshold breach 自動轉成 IncidentCase create/update，並依 binding/runtime/incident cluster 去重。

## Implementation Note
- Incident writer endpoint: `POST /api/incidents/consume-drift-report`
- Dedupe key: `binding_id + runtime_id + incident_cluster_id`
- Incident evidence now carries `telemetry_event_ids`, `reconciliation_ids`,
  binding id, runtime id, and cluster id.
- Evidence note:
  `docs/deployment/evidence/loop-auto-tel-004-drift-report-incident-classification.md`

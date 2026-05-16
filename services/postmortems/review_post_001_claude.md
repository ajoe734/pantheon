# POST-001 Review — Postmortem schema + endpoint

Reviewer: Claude
Date: 2026-05-16

## Verdict

Approved.

## What Was Reviewed

- `services/incident/postmortem.schema.json` — canonical JSON Schema (draft-07)
- `services/incident/incident.py` — domain model Postmortem class, PostmortemStatus enum, validate_postmortem, IncidentStore methods (create_postmortem, list_postmortems, get_postmortem, find_postmortem_for_incident, update_postmortem_status, link_evolution_decision)
- `services/postmortems/main.py` — FastAPI service: POST /api/postmortems, GET /api/postmortems, GET /api/postmortems/{id}, POST /api/postmortems/{id}/status, POST /api/postmortems/{id}/link-evolution-decision, GET /api/incidents/{id}/postmortem
- `services/control-plane/bff/main.py` — GET /api/v1/postmortems, GET /api/v1/postmortems/{report_id}, GET /api/v1/operator/post-incident-review/{incident_id}
- `services/control-plane/bff/read_store.py` — list_postmortems, get_postmortem, get_postmortem_by_incident with live/fallback routing

## Findings

Schema is well-formed and complete. Required fields include all lineage evidence (binding_id, deployment_stage, deployment_plan_id, capital_pool_id, persona_capital_binding_id, artifact_id, artifact_version, runtime_id, trace_id) plus root_cause. Status lifecycle (draft → review → approved → published) is enforced. additionalProperties: false prevents schema drift.

Domain layer enforces referential integrity at write time: incident_id must exist and propagated evidence must match parent IncidentCase. CanonicalReferenceValidator is applied at create time.

Postmortem service routes are correct, well-documented, and cover full CRUD plus status transitions and evolution linkage.

BFF operator read endpoints correctly delegate to read_store with live-first / local-snapshot fallback. get_post_incident_review composes postmortem, evolution decisions, lineage, and telemetry into a single operator surface. Capability gating (postmortem.read) is applied.

## Verification

All checks run independently by reviewer (Claude) on 2026-05-16:

```
python3 -m py_compile services/incident/incident.py services/postmortems/main.py \
  services/postmortems/models.py services/control-plane/bff/main.py \
  services/control-plane/bff/read_store.py
→ COMPILE OK

python3 -m pytest services/incident/test_incident.py services/postmortems/test_main_routes.py -q
→ 98 passed

python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'postmortem or post_incident'
→ 4 passed, 17 deselected
```

Known non-POST-001 signal: IN-05 idempotency-key failures in the full smoke run are pre-existing and outside this task's scope.

## Notes

None required. Returning to Codex for finalization.

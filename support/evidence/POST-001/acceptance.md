# POST-001 Acceptance Evidence

Task: POST-001 - Postmortem schema + endpoint
Owner: Codex
Reviewer: Claude
Recorded: 2026-05-16

## Scope Verified

- Canonical Postmortem schema exists at `services/incident/postmortem.schema.json`.
- Incident domain model and store enforce Postmortem lifecycle, required lineage evidence, parent IncidentCase reference, and parent evidence matching in `services/incident/incident.py`.
- Postmortem HTTP service exposes create/list/detail/status/evolution-link/incident-scoped lookup routes in `services/postmortems/main.py`.
- BFF exposes operator read endpoints:
  - `GET /api/v1/postmortems`
  - `GET /api/v1/postmortems/{report_id}`
  - `GET /api/v1/operator/post-incident-review/{incident_id}`

## Verification

```bash
python3 -m py_compile services/incident/incident.py services/postmortems/main.py services/postmortems/models.py services/control-plane/bff/main.py services/control-plane/bff/read_store.py
```

Result: passed.

```bash
python3 -m pytest services/incident/test_incident.py services/postmortems/test_main_routes.py -q
```

Result: 98 passed.

```bash
python3 services/incident/smoke_test_incident.py
```

Result: 59 passed, 0 failed.

```bash
python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'postmortem or post_incident'
```

Result: 4 passed, 17 deselected.

## Known Non-POST-001 Signal

The full `python3 services/control-plane/bff/smoke_test_incident.py` run reported the POST-001 postmortem and post-incident checks as passing, but failed five existing IN-05 operator command checks because those smoke calls omit the now-required `X-Idempotency-Key` header. That failure is outside the Postmortem schema/endpoint scope.

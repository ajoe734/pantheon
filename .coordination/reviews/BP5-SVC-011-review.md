# BP5-SVC-011 Review — Incident and Postmortem Evidence Services

**Reviewer:** Claude  
**Task:** BP5-SVC-011  
**Date:** 2026-04-15  
**Verdict:** APPROVED

---

## Acceptance Criteria

### 1. Incident and postmortem records are available through real service endpoints with evidence linkage

✅ **Met.**

- `services/incidents/main.py` exposes a full deployable FastAPI service with:
  - `POST /api/incidents` — create IncidentCase with evidence validation
  - `GET /api/incidents` — list with filters (binding_id, capital_pool_id, status, severity, open_only)
  - `GET /api/incidents/{id}` — get single
  - `POST /api/incidents/{id}/status` — lifecycle transitions (open → investigating → resolved → closed)
  - `GET /api/incidents/{id}/operator-payload` — enriched view with postmortem linkage and evolution decision ref
- `services/postmortems/main.py` exposes a full deployable FastAPI service with:
  - `POST /api/postmortems` — create Postmortem with referential integrity against IncidentCase
  - `GET /api/postmortems` — list with filters (incident_id, binding_id, status)
  - `GET /api/postmortems/{id}` — get single
  - `GET /api/postmortems/{id}/operator-payload` — enriched view with parent incident context
  - `POST /api/postmortems/{id}/status` — lifecycle transitions (draft → review → approved → published)
  - `POST /api/postmortems/{id}/link-evolution-decision` — EVO-003 reverse edge
  - `GET /api/incidents/{incident_id}/postmortem` — convenience read (at most one per incident)
- `OperatorIncidentPayload` and `OperatorPostmortemPayload` inline all canonical evidence fields plus computed convenience fields (`is_open`, `postmortem_id`, `linked_evolution_decision_id`) so operator console renders the full incident chain without cross-service joins.

### 2. Runtime binding, telemetry, and lineage references are enforced as canonical foreign references

✅ **Met.**

- `services/incident/reference_validation.py` (`CanonicalReferenceValidator`) is called at incident creation time and enforces:
  - `binding_id` must resolve through the canonical `RuntimeManagerClient` path
  - All propagated evidence fields (deployment_stage, deployment_plan_id, capital_pool_id, persona_capital_binding_id, artifact_id, artifact_version, runtime_id) must match the resolved RuntimeBinding record
  - RuntimeBinding effective_at / retired_at window must contain the incident created_at timestamp
  - `lineage_ref` must match `artifact_id@artifact_version` and be present in the canonical lineage projection for the binding
  - Each `telemetry_event_id` must resolve via the telemetry lineage path and carry the correct binding/plan/pool/artifact/runtime/trace cross-refs
- `CanonicalReferenceError` is surfaced as HTTP 422 `reference_errors` — request is rejected, not silently stored
- Postmortem creation validates that the referenced IncidentCase exists and that the parent incident itself passes canonical reference validation before the postmortem is accepted

---

## Route Tests

- **23 route tests** in `services/incidents/test_main_routes.py` — all pass
- **23 route tests** in `services/postmortems/test_main_routes.py` — all pass
- **Total: 46/46 tests pass** (verified locally: `python3 -m pytest services/incidents/test_main_routes.py services/postmortems/test_main_routes.py`)

Test coverage includes: happy-path create/list/get, duplicate 409, invalid enum 422, reference error rejection, lifecycle transitions, auto resolved_at/published_at, operator payloads, EVO-003 link, convenience postmortem-for-incident route, and 404 paths.

---

## L1 Policy Compliance

| Policy Point | Status | Location |
|---|---|---|
| Canonical foreign-ref enforcement (EVOLUTION_REVIEW §3.1) | ✅ | `CanonicalReferenceValidator.validate_incident` |
| Incident write authority scoped to Incident domain only | ✅ | Documented in module docstring; only `services/incidents/main.py` calls `store.create_incident` |
| Postmortem referential integrity against IncidentCase | ✅ | `store.create_postmortem` in INC-001 backbone enforces `incident_id` exists and evidence fields match |
| Operator payload inlines all evidence fields (no join needed at console) | ✅ | `OperatorIncidentPayload`, `OperatorPostmortemPayload` |
| EVO-003 reverse edge: postmortem ↔ evolution decision | ✅ | `POST /api/postmortems/{id}/link-evolution-decision` |
| Lineage ref must match artifact snapshot | ✅ | `reference_validation.py` lines 206–219 |
| Telemetry event IDs verified against canonical lineage path | ✅ | `_telemetry_trace_mismatch_errors` |
| DB write boundary: postmortem service shares store, not bypass | ✅ | Both services use the same `IncidentStore`; production path will share Postgres schema |

---

## Minor Observations (Non-blocking)

1. `services/incidents/main.py` calls `store.find_postmortem_for_incident` to populate `postmortem_id` in the operator payload, but that method lives in the `IncidentStore`. In production with separate Postgres schemas, the postmortem lookup from the incident service would need a cross-schema read or an HTTP call to the postmortem service. This is a noted production concern; acceptable for the v1 file-based store.
2. `IncidentSeverityEnum` and `IncidentStatusEnum` in `services/incidents/models.py` subclass `str` directly rather than using `enum.Enum`. This means they do not get Pydantic automatic coercion benefits. Validation is handled by the INC-001 backbone's `validate_incident_case`, so this is not a correctness issue.
3. `services/postmortems/main.py` imports `_get_incident_for_postmortem_or_404` but only uses it in the helper definition; the `get_operator_payload` route calls it implicitly. Clean pattern; no issue.

None of these require changes before approval.

---

## Downstream Unblocked

Approval of this task unblocks:
- `BP5-SVC-013` (runtime manager kill-switch and safe-mode service)
- `BP5-SVC-015` (BFF read store and control-plane resilience)
- UI tasks `BP5-LUV-002`, `BP5-LUV-003`, `BP5-LUV-004`, `PKT-002-incident-home`, `PKT-002-incident-detail`, `PKT-002-incident-action-drawer`, `PKT-003-post-incident-review`

# BP5-SVC-011 Acceptance Packet

**Task:** BP5-SVC-011 — Realize incident and postmortem evidence services  
**Sidecar:** BP5-SVC-011-SIDECAR-ACCEPTANCE  
**Owner (sidecar):** Claude  
**Reviewer (sidecar):** Codex  
**Parent owner:** Claude  
**Parent reviewer:** Codex  
**Date prepared:** 2026-04-16  
**Status:** reviewer-validated

---

## 1. Scope Reminder

This packet is a **support artifact only**. It does not modify L1 canonical truth,
core contracts, or runtime/registry/governance implementations. The parent task
(BP5-SVC-011) realization is assessed here; no code changes are made in this sidecar.

---

## 2. Dependency Status

| Dependency | Title | Status |
|---|---|---|
| BP5-SVC-009 | Realize telemetry ingest service and shock-absorption path | **done** |
| BP5-SVC-010 | Realize the lineage read model and performance service path | **done** |

Both upstream dependencies are complete. BP5-SVC-011 has no outstanding blockers
from the dependency chain.

---

## 3. Artifact Inventory

### 3.1 Domain Layer — INC-001 Backbone (`services/incident/`)

| File | Purpose |
|---|---|
| `incident.py` | `IncidentCase`, `Postmortem`, `IncidentStore`, status FSMs, validation |
| `reference_validation.py` | `CanonicalReferenceValidator` — enforces canonical foreign references |
| `incident_case.schema.json` | JSON Schema for IncidentCase wire shape |
| `postmortem.schema.json` | JSON Schema for Postmortem wire shape |
| `contract.md` | INC-001 domain contract (write authority, referential integrity rules) |
| `test_incident.py` | Domain and store/unit coverage (75 cases) |
| `test_reference_validation.py` | Canonical reference validation coverage (6 cases) |
| `smoke_test_incident.py` | Live smoke test exercising create/status/postmortem flow |

### 3.2 Incident Evidence Service (`services/incidents/`)

| File | Purpose |
|---|---|
| `main.py` | FastAPI app — authoritative HTTP surface for IncidentCase CRUD + operator payload |
| `models.py` | Wire-layer Pydantic models (`CreateIncidentRequest`, `IncidentResponse`, `OperatorIncidentPayload`) |
| `test_main_routes.py` | Route integration tests (23 cases) |
| `requirements.txt` | Service dependencies |

### 3.3 Postmortem Evidence Service (`services/postmortems/`)

| File | Purpose |
|---|---|
| `main.py` | FastAPI app — authoritative HTTP surface for Postmortem CRUD + evolution linkage |
| `models.py` | Wire-layer Pydantic models (`CreatePostmortemRequest`, `PostmortemResponse`, `OperatorPostmortemPayload`) |
| `test_main_routes.py` | Route integration tests (23 cases) |
| `requirements.txt` | Service dependencies |

---

## 4. Acceptance Checklist

### 4.1 "Incident and postmortem records available through real service endpoints with evidence linkage"

| Check | Result |
|---|---|
| `POST /api/incidents` creates IncidentCase with all required evidence fields | PASS |
| `GET /api/incidents` lists with filter by `binding_id`, `capital_pool_id`, `status`, `severity`, `open_only` | PASS |
| `GET /api/incidents/{id}` returns single record, 404 on missing | PASS |
| `POST /api/incidents/{id}/status` enforces FSM (open → investigating → resolved → closed); auto-sets `resolved_at` | PASS |
| `GET /api/incidents/{id}/operator-payload` returns enriched view with `postmortem_id` and `linked_evolution_decision_id` when available | PASS |
| `POST /api/postmortems` creates Postmortem linked to IncidentCase | PASS |
| `GET /api/postmortems` lists with filter by `incident_id`, `binding_id`, `status` | PASS |
| `POST /api/postmortems/{id}/status` enforces FSM (draft → review → approved → published); auto-sets `published_at` | PASS |
| `POST /api/postmortems/{id}/link-evolution-decision` sets `linked_evolution_decision_id` (EVO-003 callback) | PASS |
| `GET /api/incidents/{id}/postmortem` convenience endpoint returns linked postmortem or null | PASS |
| `GET /api/postmortems/{id}/operator-payload` enriches postmortem with parent incident status/severity/timestamps | PASS |
| `GET /__health__` liveness probe on both services | PASS |

### 4.2 "Runtime binding, telemetry, and lineage references enforced as canonical foreign references"

| Check | Result |
|---|---|
| `IncidentCase` carries `binding_id`, `deployment_stage`, `deployment_plan_id`, `capital_pool_id`, `persona_capital_binding_id`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id` as required fields | PASS |
| `CanonicalReferenceValidator` validates presence of required canonical foreign references at create time | PASS |
| `Postmortem` inherits and must match all evidence fields from the parent `IncidentCase` (mismatch → 422) | PASS |
| Duplicate incident/postmortem IDs rejected with 409 | PASS |
| Postmortem creation enforces referential integrity: `incident_id` must exist | PASS |
| `telemetry_event_ids` list carried on `IncidentCase` as explicit telemetry evidence linkage | PASS |
| `lineage_ref` field on `IncidentCase` provides optional lineage evidence pointer (from BP5-SVC-010 read model) | PASS |

### 4.3 Test Coverage

```
127 passed in 1.49s
```

Reviewer re-validation on 2026-04-16 confirms the current test inventory:

- `services/incident/test_incident.py` — 75 collected / passing
- `services/incident/test_reference_validation.py` — 6 collected / passing
- `services/incidents/test_main_routes.py` — 23 collected / passing
- `services/postmortems/test_main_routes.py` — 23 collected / passing

Command used:

```bash
pytest -q services/incident/test_incident.py \
  services/incident/test_reference_validation.py \
  services/incidents/test_main_routes.py \
  services/postmortems/test_main_routes.py
```

No failures or skips were observed in this reviewer run.

---

## 5. Dependency Map

```
BP5-SVC-009 (telemetry ingest, done)
    └── telemetry_event_ids linkage on IncidentCase
    └── shock-absorption path upstream of incident evidence

BP5-SVC-010 (lineage read model, done)
    └── lineage_ref field on IncidentCase
    └── Postmortem evidence propagation inherits lineage context

BP5-SVC-011 (this task)
    ├── services/incident/  ← INC-001 domain backbone
    ├── services/incidents/ ← Incident Evidence Service (port 8090)
    └── services/postmortems/ ← Postmortem Evidence Service (port 8091)

Downstream dependents (blocked on BP5-SVC-011):
    BP5-SVC-013 — kill-switch and operational evolution orchestration
    BP5-SVC-015 — BFF snapshot removal
    BP5-WB-006  — Knowledge Workbench packetization
    BP5-LUV-003 — incident-home Lovable loop
    BP5-LUV-004 — incident-detail Lovable loop
    BP5-LUV-005 — incident-action-drawer Lovable loop
    BP5-LUV-008 — post-incident-review Lovable loop
```

---

## 6. Gap Analysis

### Realized (within BP5-SVC-011 scope)

- `IncidentCase` backbone object with all required canonical foreign references
- `Postmortem` backbone object with referential integrity against IncidentCase
- Evidence linkage read path via `OperatorIncidentPayload` and `OperatorPostmortemPayload`
- Status FSMs for both incident lifecycle and postmortem lifecycle
- Evolution decision reverse linkage (`link-evolution-decision` route, EVO-003 callback)
- Canonical reference validation at write time
- Full test coverage (127 tests, all passing)

### Out of Scope for BP5-SVC-011 (expected gaps for downstream tasks)

| Gap | Owner task |
|---|---|
| BFF read-store integration for incident/postmortem queries | BP5-SVC-015 |
| Operator console UI screens (incident-home, incident-detail, action-drawer) | BP5-LUV-003/004/005 |
| Post-incident review console | BP5-LUV-008 |
| Kill-switch and freeze flows that cite incident evidence | BP5-SVC-013 |
| Knowledge Workbench evidence reference surfaces | BP5-WB-006 |

These are intentional downstream tasks, not missing work in BP5-SVC-011 itself.

---

## 7. Reviewer Handoff Notes

- **No canonical truth was modified** by this sidecar. All assessments are observational.
- The parent task BP5-SVC-011 can be considered **implementation-complete** based on:
  - Both service HTTP surfaces realized
  - All acceptance criteria met
  - All tests passing
  - Evidence linkage to BP5-SVC-009 (telemetry) and BP5-SVC-010 (lineage) confirmed
- If Codex (reviewer) agrees, the parent owner (Claude) may transition BP5-SVC-011 to `done`.
- This sidecar (BP5-SVC-011-SIDECAR-ACCEPTANCE) should follow its own lifecycle:
  `review → review_approved → done` per collaboration guide.

---

## 8. L1 Policy Alignment

The realized services align with these L1 canonical documents:

| Document | Alignment point |
|---|---|
| `EVOLUTION_REVIEW_AND_THRESHOLDS.md` | Postmortem `linked_evolution_decision_id` forms the evidence chain for evolution governance |
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | `telemetry_event_ids` and `lineage_ref` fields use the canonical lineage pointer model |
| `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` | Postmortem creation enforces referential integrity at write time (no orphaned postmortems) |
| `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | Incident domain (INC-001) is sole write authority for IncidentCase and Postmortem records |
| `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` | Telemetry event ID list on IncidentCase provides explicit event evidence references |

---

## 9. Reviewer Validation Addendum

- Reviewer: Codex
- Review date: 2026-04-16
- Verdict: packet content accepted after correcting stale test-count claims
- Approval basis:
  - support artifact stays within sidecar scope
  - artifact inventory and acceptance checklist match the reviewed service/test layout
  - targeted reviewer test run confirms the packet's acceptance claim set

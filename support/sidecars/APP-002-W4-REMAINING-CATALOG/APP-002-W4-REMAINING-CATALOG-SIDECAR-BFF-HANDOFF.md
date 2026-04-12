# APP-002-W4-REMAINING-CATALOG BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W4-REMAINING-CATALOG` - Finish remaining contractual list/detail read surfaces  
**Parent Owner**: Qwen  
**Parent Reviewer**: Codex  
**Parent Status**: `done`  
**Sidecar Owner**: Claude (auto-generated lane; superseded during stale approval cleanup)  
**Sidecar Reviewer**: Codex  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-04-12  
**Last Updated**: 2026-04-12  
**Review Status**: absorbed by Codex during stale approval cleanup

> Support artifact only. This packet summarizes the Wave 4 remaining catalog surfaces and packages them for downstream frontend and operator-surface consumers. It does not change canonical truth, policy, or runtime/governance authority.

---

## 1. Parent Task Summary

`APP-002-W4-REMAINING-CATALOG` closed the contract gap between `BFF_API_CONTRACT.md` and the executable read surfaces in `services/control-plane/bff/main.py`.

Wave 4 focus:

- finish remaining persona list/detail/session/capability surfaces
- finish the remaining catalog-style list/detail routes for capital, deployment, runtime, telemetry, lineage, incident, and evolution
- keep the implementation aligned with the canonical 33-surface inventory

Primary implementation anchors:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_w4_remaining_catalog.py`
- `docs/examples/bff-remaining-catalog.json`
- `services/control-plane/bff/BFF_API_CONTRACT.md`

---

## 2. Surface Inventory

The following contractual read surfaces are live in `main.py` and backed by `ReadSurfaceStore`.

### 2.1 Persona Surfaces

| Surface | Endpoint | Purpose |
|---|---|---|
| PS-01 | `GET /api/v1/personas` | persona catalog |
| PS-02 | `GET /api/v1/personas/{persona_id}` | persona detail with bindings |
| PS-03 | `GET /api/v1/personas/{persona_id}/sessions` | session list for a persona |
| PS-04 | `GET /api/v1/sessions/{session_id}` | single session detail |
| PS-05 | `GET /api/v1/personas/{persona_id}/teaching` | teaching-session list |
| PS-06 | `GET /api/v1/personas/{persona_id}/capabilities` | capability snapshot |

### 2.2 Capital / Binding / Deployment / Runtime Catalog

| Surface | Endpoint | Purpose |
|---|---|---|
| CP-01 | `GET /api/v1/capital-pools` | capital-pool list |
| CP-02 | `GET /api/v1/capital-pools/{pool_id}` | capital-pool detail with bindings |
| CP-03 | `GET /api/v1/bindings` | binding list |
| CP-04 | `GET /api/v1/bindings/{binding_id}` | binding detail with persona |
| DP-01 | `GET /api/v1/deployment-plans` | deployment-plan list |
| DP-02 | `GET /api/v1/deployment-plans/{plan_id}` | deployment-plan detail with approval decision |
| DP-03 | `GET /api/v1/approval-decisions` | approval-decision list |
| DP-04 | `GET /api/v1/approval-decisions/{decision_id}` | approval-decision detail |
| RT-01 | `GET /api/v1/runtime-bindings` | runtime-binding list |
| RT-02 | `GET /api/v1/runtime-bindings/{binding_id}` | runtime-binding detail with deployment plan |
| RT-03 | `GET /api/v1/runtimes/{runtime_id}/status` | runtime status by runtime id |
| RT-04 | `GET /api/v1/runtimes/{runtime_id}/rollbacks` | rollback history for a runtime |

### 2.3 Telemetry / Lineage / Incident / Evolution Catalog

| Surface | Endpoint | Purpose |
|---|---|---|
| TL-01 | `GET /api/v1/telemetry` | telemetry-event list |
| TL-02 | `GET /api/v1/telemetry/{runtime_id}/summary` | runtime telemetry summary |
| TL-03 | `GET /api/v1/telemetry/{artifact_id}/performance` | artifact performance view |
| LN-01 | `GET /api/v1/lineage` | lineage edge list |
| LN-02 | `GET /api/v1/lineage/edges/{edge_id}` | single lineage edge |
| LN-03 | `GET /api/v1/lineage/graph` | lineage graph traversal |
| IN-01 | `GET /api/v1/incidents` | incident list |
| IN-02 | `GET /api/v1/incidents/{incident_id}` | incident detail |
| IN-03 | `GET /api/v1/postmortems` | postmortem list |
| IN-04 | `GET /api/v1/postmortems/{report_id}` | postmortem detail |
| IN-05 | `GET /api/v1/kill-switch/status` | active freeze orders + affected runtimes |
| EV-01 | `GET /api/v1/evolution-decisions` | evolution-decision list |
| EV-02 | `GET /api/v1/evolution-decisions/{decision_id}` | evolution-decision detail |
| EV-03 | `GET /api/v1/freeze-orders` | freeze-order list |
| EV-04 | `GET /api/v1/rollbacks` | global rollback catalog |

---

## 3. Frontend / Operator Handoff Notes

This wave is mostly catalog coverage rather than a single page-shaped operator view. The intended downstream use is:

- selector and lookup drawers
- detail pages that do not need client-side joins
- supporting panels for incident, persona, and evolution workbenches
- fallback/detail inspection when a composed view is degraded or unavailable

Recommended consumption rules:

- prefer the composed operator views when they exist (`deployment-review`, `incident-response`, `persona-management`)
- use these list/detail routes for secondary panels, drilldowns, and search/filter workflows
- treat all `meta` payloads as authoritative for freshness / degradation messaging
- do not invent joins that duplicate server-shaped fields already present in detail endpoints

---

## 4. Example Payload Packet

`docs/examples/bff-remaining-catalog.json` is the example bundle for this slice. It contains request/response examples for the high-value catalog routes:

- persona list/detail/sessions/teaching/capabilities
- capital pools and bindings
- deployment plans and approval decisions
- runtime bindings

This example bundle should be used as the first reference for frontend type alignment or UI scaffolding when no page-specific packet exists yet.

---

## 5. Verification Snapshot

Verification re-run during stale approval cleanup:

- `python3 services/control-plane/bff/test_w4_remaining_catalog.py`

Coverage confirmed by the test:

- PS-01 through PS-06
- CP-01 and CP-03
- DP-01, DP-03, DP-04
- RT-01 and RT-03

The broader route inventory is also present in `main.py` and reflected in the parent task closeout note in `current-work.md`.

---

## 6. Non-Blocking Notes

1. This packet was created during cleanup of a stale approval-blocked sidecar worker, not by the original Claude lane.
2. The parent implementation was already complete; the missing piece was this support packet plus queue/task cleanup.
3. No new canonical requirements are introduced here; this is a packaging artifact for downstream consumption.

---

## 7. Handoff Status

Packet prepared and absorbed during stale approval cleanup. No additional worker run is required for this sidecar.

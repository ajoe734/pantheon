# DEPLOY-002 BFF & Frontend Handoff Packet

**Task ID**: DEPLOY-002-SIDECAR-BFF-HANDOFF
**Parent Task**: DEPLOY-002 (phase6-2026-04-16-oss-ecosystem-closure)
**Helper Kind**: bff_handoff_packet
**Owner**: Claude
**Reviewer**: Codex
**Created**: 2026-04-17
**Revised**: 2026-04-17 (v2 — aligns compose wiring, port, RBAC, and error codes with current implementation)
**Status**: ready-for-review

> **Scope boundary**: This is a support artifact only. It does not modify canonical truth, L1 policy files, or the main BFF implementation. Its purpose is to surface BFF query gaps, operator journey readiness, and frontend handoff materials so Codex (parent owner) can absorb relevant findings into DEPLOY-002 finalization.

---

## 1. Executive Summary

The governed BFF (`services/control-plane/bff`) is **functionally complete** against the v1 API contract (`BFF_API_CONTRACT.md`). All 33 L1 canonical read surfaces, 4 composed operator views, 3 SSE stream endpoints, and the command submission surface are implemented in `main.py`.

**Key finding**: The compose wiring (env vars + volumes) is **already configured** in `docker-compose.yml`. BFF reads governance/runtime data from named volumes shared with downstream services. No code changes and no new env var additions are required for DEPLOY-002 scope.

---

## 2. Implemented Surface Inventory

### 2.1 L1 Canonical Read Surfaces (all 33 present)

| Domain | Surface IDs | Routes | Status |
|---|---|---|---|
| Persona (PS) | PS-01–PS-06 | `/api/v1/personas`, `/api/v1/sessions/{id}`, `/api/v1/personas/{id}/teaching`, `/api/v1/personas/{id}/capabilities` | ✅ |
| Capital Pool & Binding (CP) | CP-01–CP-04 | `/api/v1/capital-pools`, `/api/v1/bindings` | ✅ |
| Deployment (DP) | DP-01–DP-04 | `/api/v1/deployment-plans`, `/api/v1/approval-decisions` | ✅ |
| Runtime (RT) | RT-01–RT-04 | `/api/v1/runtime-bindings`, `/api/v1/runtimes/{id}/status`, `/api/v1/runtimes/{id}/rollbacks` | ✅ |
| Telemetry (TL) | TL-01–TL-03 | `/api/v1/telemetry`, `/api/v1/telemetry/{runtime_id}/summary`, `/api/v1/telemetry/{artifact_id}/performance` | ✅ |
| Lineage (LN) | LN-01–LN-03 | `/api/v1/lineage`, `/api/v1/lineage/edges/{id}`, `/api/v1/lineage/graph` | ✅ |
| Incident (IN) | IN-01–IN-05 | `/api/v1/incidents`, `/api/v1/postmortems`, `/api/v1/kill-switch/status` | ✅ |
| Evolution (EV) | EV-01–EV-04 | `/api/v1/evolution-decisions`, `/api/v1/freeze-orders`, `/api/v1/rollbacks` | ✅ |

### 2.2 Composed Operator Views (all 4 present)

| Route | Journey | Status |
|---|---|---|
| `/api/v1/operator/deployment-review/{plan_id}` | Pre-deployment review | ✅ |
| `/api/v1/operator/incident-response/{incident_id}` | Incident response | ✅ |
| `/api/v1/operator/post-incident-review/{incident_id}` | Post-incident analysis | ✅ |
| `/api/v1/operator/persona-management/{persona_id}` | Persona lifecycle management | ✅ |

### 2.3 SSE Real-Time Feeds (all 3 present)

| Endpoint | Stream | Status |
|---|---|---|
| `/api/v1/runtime/{runtime_id}/events/stream` | Runtime state changes | ✅ |
| `/api/v1/incidents/stream` | Active incident events | ✅ |
| `/api/v1/kill-switch/updates` | Kill-switch state changes | ✅ |

### 2.4 Additional Surfaces (beyond v1 contract)

The BFF exposes several surfaces beyond the API contract v1 scope. These are not gaps — they are additions:

| Route | Purpose |
|---|---|
| `/api/v1/operator/governance/review-queue` | Governance review queue surface |
| `/api/v1/operator/rollback-review/{rollback_id}` | Rollback review surface |
| `/api/v1/operator/governance/audit` | Governance audit surface |
| `/api/v1/personas/{id}/consultations` | Consultation surfaces (APP-001C) |
| `/api/v1/consultations/{session_id}` | Consultation detail |
| `/api/v1/consultations/{session_id}/participants` | Consultation participants |
| `/api/v1/consultations/{session_id}/outcome` | Consultation outcome |
| `/api/v1/consultations/{session_id}/evidence` | Consultation evidence |
| `/api/v1/personas/{id}/consult-policy` | Consultation policy surface |
| `/api/v1/operator/degraded-control-guidance` | Degraded operator control guidance |
| `/api/v1/operator/commands` (POST) | Command submission |
| `/api/v1/operator/commands/{id}` (GET) | Command status check |
| `/api/v1/internal/sse/publish` (POST) | Internal SSE publish (internal only) |

---

## 3. BFF Query Gaps (Backend Data Wiring)

### 3.1 Compose Wiring — Already Configured

The `docker-compose.yml` `operator-bff` service entry already includes all required env vars and named volumes:

```yaml
environment:
  BFF_DATA_DIR: /data/bff
  BFF_READ_SURFACE_STATE: fresh
  PANTHEON_GOVERNANCE_DATA_DIR: /data/governance
  PANTHEON_RUNTIME_DATA_DIR: /data/runtime
  INCIDENTS_DATA_DIR: /data/incidents
  POSTMORTEMS_DATA_DIR: /data/incidents
  PANTHEON_INTERNAL_API_URL: http://runtime-manager:8081
  DATABASE_URL: ${DATABASE_URL:-postgresql://...}
  PANTHEON_NATS_URL: ${PANTHEON_NATS_URL:-nats://nats:4222}
volumes:
  - bff-data:/data/bff
  - governance-data:/data/governance:ro
  - runtime-data:/data/runtime:ro
  - incident-data:/data/incidents:ro
```

No additional compose edits are required for DEPLOY-002 scope.

### 3.2 Canonical Snapshot Adapter (what's wired)

The `CanonicalSnapshotAdapter` in `read_store.py` reads from JSON files via these environment variables. All are set in compose:

| Dataset | Env Var | File |
|---|---|---|
| `deployment_plans` | `PANTHEON_GOVERNANCE_DATA_DIR` | `deployment_plans.json` |
| `approval_decisions` | `PANTHEON_GOVERNANCE_DATA_DIR` | `approval_decisions.json` |
| `capital_pools` | `PANTHEON_GOVERNANCE_DATA_DIR` | `capital_pools.json` |
| `persona_bindings` | `PANTHEON_GOVERNANCE_DATA_DIR` | `persona_capital_bindings.json` |
| `runtime_bindings` | `PANTHEON_RUNTIME_DATA_DIR` | `runtime_bindings.json` |
| `incidents` | `INCIDENTS_DATA_DIR` | `incidents.json` |
| `postmortems` | `POSTMORTEMS_DATA_DIR` | `postmortems.json` |

### 3.3 Local Read Store (snapshot-only, no live service call)

The following surface domains read from local JSON snapshot files. They are **not wired to live downstream HTTP services** — this is expected for paper trading stage:

| Domain | Data Source | Gap Level |
|---|---|---|
| Personas | Local `read_surfaces.json` + env snapshot | P2 — informational |
| Telemetry | Local `read_surfaces.json` | P2 — informational |
| Lineage | Local `read_surfaces.json` | P2 — informational |
| Evolution decisions | Local `read_surfaces.json` | P2 — informational |
| Freeze orders | Local `read_surfaces.json` | P2 — informational |
| Rollback records | Local `read_surfaces.json` | P1 — governance-critical (freshness) |
| Kill-switch status | Local `read_surfaces.json` | P0 — operational safety |
| Runtime status | Local `read_surfaces.json` | P0 — operational safety |
| Incident status | `INCIDENTS_DATA_DIR` snapshot | P0 — operational safety |

### 3.4 Gap Classification

| Gap Level | Description | Implication |
|---|---|---|
| **P0 — operational safety** | Kill-switch status, runtime status, incident status | Must be wired to live services before canary/live deployment; acceptable for paper trading |
| **P1 — governance-critical** | Deployment plans, approval decisions, bindings, capital pools | Canonical snapshot adapter covers this; gap is freshness guarantee |
| **P2 — informational** | Telemetry, lineage, evolution decisions, postmortems | Acceptable to serve from local snapshot for paper trading stage |

### 3.5 Post-DEPLOY-002 Live Wiring

For post-paper live deployment, add HTTP client calls from BFF to:
- `persona` service (PS surfaces)
- `incidents`/`postmortems` services (beyond volume-backed snapshot)
- `runtime-manager` (RT-03 status, kill-switch state push)
- `telemetry`, `lineage-read`, `evolution` (P2 surfaces)

This is a code-level task deferred to after DEPLOY-002 stack stabilization.

---

## 4. Operator Journey Readiness

Each operator journey from `BFF_SURFACE_INVENTORY.md §4` maps to a composed view endpoint. Status as of DEPLOY-002:

### 4.1 Pre-Deployment Review Journey
```
DP-03 → DP-02 → CP-02 → CP-04 → RT-02 → RT-04
```
**Endpoint**: `GET /api/v1/operator/deployment-review/{plan_id}`
**Status**: ✅ Route implemented. Data available via `governance-data` named volume.
**Frontend note**: Response shape includes `meta.surfaces` for partial-rendering support. UI should handle per-surface `"status": "degraded"` and `"status": "unavailable"` explicitly instead of collapsing them into "no data".

### 4.2 Incident Response Journey
```
IN-01 → IN-02 → RT-03 → TL-02 → RT-04 → EV-04 → IN-05
```
**Endpoint**: `GET /api/v1/operator/incident-response/{incident_id}`
**Status**: ✅ Route implemented. Kill-switch status (IN-05) requires `admin` role. Incident data backed by `incident-data` volume.
**Frontend note**: SSE stream `/api/v1/incidents/stream` provides live push. Frontend should subscribe on incident detail page load.

### 4.3 Post-Incident Review Journey
```
IN-03 → IN-04 → EV-01 → EV-02 → LN-01 → TL-03
```
**Endpoint**: `GET /api/v1/operator/post-incident-review/{incident_id}`
**Status**: ✅ Route implemented. Lineage and telemetry data depend on local snapshot presence.
**Frontend note**: This is a read-heavy, non-real-time view. No SSE needed. Paginate lineage edges if graph depth > 3.

### 4.4 Persona Management Journey
```
PS-01 → PS-02 → CP-03 → CP-04 → PS-03 → PS-05
```
**Endpoint**: `GET /api/v1/operator/persona-management/{persona_id}`
**Status**: ✅ Route implemented.
**Frontend note**: Persona binding data comes from `governance-data` named volume.

---

## 5. Frontend Handoff Materials

### 5.1 API Base URL

In docker-compose context (DEPLOY-002 stack):
- Internal (service-to-service): `http://operator-bff:8001`
- Host-mapped (browser/dev): `http://localhost:18001`

> Port `8001` is the BFF's listen port (confirmed `main.py:3487` and compose `ports: 18001:8001`). Do not use `8000`.

### 5.2 Authentication

Stub token format for development/testing:
```
Authorization: Bearer <operator_id>:<comma_roles>[:mfa]
```
Examples:
- `Bearer op-01:operator` — standard operator
- `Bearer op-admin:admin,operator:mfa` — admin with MFA verified
- `Bearer reviewer-1:reviewer` — reviewer-grade read access
- `Bearer approver-1:approver` — approval-only role (deployment and rollback approvals)

> For production, this will be replaced by JWT verification. Frontend should abstract auth header construction behind an auth service. Note: a `viewer`-only token is parseable by `_extract_identity()`, but the current build gates read surfaces behind operator-level roles (`operator`, `reviewer`, `approver`, `admin`), so `viewer` alone is not sufficient.

### 5.3 Response Families (current implementation)

The BFF does not use one universal envelope for every route. Frontend integration should expect three main response families:

**Family A — `data` + `meta`** (most list/detail surfaces):
```json
{
  "data": [ { "id": "...", "type": "...", "_links": {} } ],
  "meta": { "total": 0, "staleness": null }
}
```

**Family B — `items` + `page_info` + `meta`** (paged catalog surfaces such as incidents and evolution decisions):
```json
{
  "items": [ { "id": "...", "type": "...", "_links": {} } ],
  "page_info": { "next_page_token": null },
  "meta": { "snapshot_at": "2026-04-17T22:00:00Z", "staleness": null }
}
```

**Family C — composed views** (partial rendering support; may include extra top-level keys such as `allowedActions`):
```json
{
  "data": { "deployment_plan": {}, "capital_pool": {}, "... sub-surfaces ...": {} },
  "allowedActions": { "canPause": true },
  "meta": {
    "snapshot_at": "2026-04-17T22:00:00Z",
    "surfaces": {
      "deployment_plan": { "status": "ok" },
      "runtime_binding": { "status": "degraded", "staleness": { "served_from": "cache", "last_known_at": "..." } },
      "kill_switch": { "status": "unavailable", "staleness": { "served_from": "unverifiable", "last_known_at": "..." } }
    }
  }
}
```

### 5.4 Degradation Handling (UI Contract)

| Signal | UI Behavior |
|---|---|
| `meta.staleness` absent or `null` | Render normally |
| surface status `"degraded"` | Show a stale/degraded badge using `staleness.served_from` and `last_known_at` |
| surface status `"unavailable"` | Show an explicit unavailable placeholder for that panel; keep the rest of the page visible |
| `meta.degradation` present | Surface the route-level warning text in addition to panel-level status |

**Key rule**: Never show "no data" when a surface status is `"degraded"` or `"unavailable"`. Always show the state explicitly. This matches the BFF's "never show none" behavior for unverifiable data.

### 5.5 Pagination

| Parameter | Default | Max |
|---|---|---|
| `page_token` | none | opaque offset token |
| `page_size` | 20 | 200 on paged routes |
| `depth` (lineage) | 3 | 10 |
| `time_range` (telemetry) | optional | validated per route |

### 5.6 RBAC — Role Requirements by Surface

The effective roles enforced by `main.py` are `operator`, `reviewer`, `approver`, `admin`. Role checks are enforced server-side; the frontend must render affordances conditionally.

**Read surfaces**:

| Surface Group | Minimum Role |
|---|---|
| All standard read surfaces (PS, CP, DP, RT, TL, LN, IN-01–IN-04, EV) | any operator-level role: `operator`, `reviewer`, `approver`, or `admin` |
| Kill-switch status (IN-05) | `admin` |
| All composed operator views | any operator-level role: `operator`, `reviewer`, `approver`, or `admin` |

**Command surfaces** (POST `/api/v1/operator/commands`):

| Command | Required Role |
|---|---|
| `PauseExecution`, `IssueRiskOff`, `PauseRuntime` | `operator` or `admin` |
| `LiquidateAll`, `IssueSafeMode` | `admin` + MFA required |
| `ApproveDeployment`, `ExecuteRollback`, `ApproveRollback`, `RejectRollback` | `approver` or `admin` |
| `HardRollback` | `admin` or `approver` |
| `ActivateKillSwitch` | `admin` + MFA required |
| `ApproveEvolutionDecision` | `reviewer`, `approver`, or `admin` |
| `ExecuteEvolutionAction` | `approver` or `admin` |

`GET /api/v1/operator/commands/{id}` only requires an authenticated Bearer token; it does not apply the shared read-role gate.

### 5.7 SSE Integration

For real-time surfaces, subscribe via SSE:
```
GET /api/v1/runtime/{runtime_id}/events/stream
GET /api/v1/incidents/stream
GET /api/v1/kill-switch/updates
```

Reconnection: pass `?last_event_id=<last_seen_id>` for replay.

Event shape:
```json
{
  "id": "evt-...",
  "type": "runtime_state_changed | incident_created | kill_switch_activated | ...",
  "timestamp": "2026-04-17T22:00:00Z",
  "data": { "runtime_id": "r-001", "current_state": "paper", "surface_id": "RT-03" }
}
```

### 5.8 Error Codes Reference

Error codes match the `ErrorCode` enum in `services/control-plane/bff/models.py`:

| Code | HTTP | When |
|---|---|---|
| `INVALID_TOKEN` | 401 | Missing or invalid Authorization header |
| `INSUFFICIENT_ROLE` | 403 | Operator lacks required role for the surface |
| `MFA_REQUIRED` | 403 | Command requires MFA-verified token (e.g. kill-switch) |
| `OBJECT_NOT_FOUND` | 404 | Resource ID does not exist |
| `INVALID_PARAMS` | 422 | Bad query params, invalid field, or time range violation |
| `INVALID_STATE` | 422 | Resource in wrong state for the requested operation |
| `PRECONDITION_NOT_MET` | 409 | Command precondition failed (e.g. binding conflict) |
| `CONCURRENT_MODIFICATION` | 409 | Optimistic-lock conflict on the resource |
| `DOWNSTREAM_UNAVAILABLE` | 503 | Backend service unreachable |

---

## 6. DEPLOY-002 Compose Integration Checklist

| Item | Required For | Status |
|---|---|---|
| `operator-bff` healthcheck passes | DEPLOY-002 acceptance | ✅ Dockerfile + `/health` endpoint present |
| Port mapping `18001:8001` in docker-compose.yml | Host-accessible BFF | ✅ Already configured |
| `BFF_DATA_DIR` env var + `bff-data` named volume | Command log persistence | ✅ Already configured (`/data/bff`) |
| `PANTHEON_GOVERNANCE_DATA_DIR` + `governance-data:ro` volume | Deployment/binding/capital data | ✅ Already configured (`/data/governance`) |
| `PANTHEON_RUNTIME_DATA_DIR` + `runtime-data:ro` volume | Runtime binding data | ✅ Already configured (`/data/runtime`) |
| `INCIDENTS_DATA_DIR` + `incident-data:ro` volume | Incident/postmortem data | ✅ Already configured (`/data/incidents`) |
| `PANTHEON_INTERNAL_API_URL` → `runtime-manager:8081` | Command backend routing | ✅ Already configured |
| Cross-service HTTP clients (persona, telemetry, etc.) | Live data (post-paper) | 🔲 Deferred to post-DEPLOY-002 |

Legend: ✅ done · 🔲 deferred

---

## 7. Handoff Notes for Codex (Parent Owner)

1. **No canonical truth was modified** in this sidecar. All findings are observational.
2. **Surface coverage is complete** — the BFF implementation matches the API contract v1.
3. **All compose wiring is already in place** — env vars, volumes, port mapping, and health check are configured in `docker-compose.yml`. No compose edits are needed for DEPLOY-002 scope.
4. **Port correction applied**: internal URL is `http://operator-bff:8001`, host-mapped to `http://localhost:18001`. Earlier draft incorrectly listed `8000`.
5. **Frontend can start integration** against the existing BFF using the handoff materials in section 5. For local development, use `operator`, `reviewer`, `approver`, or `admin` roles in the stub token format; `viewer` alone will not pass the current read-role gate.
6. **RBAC, auth examples, and error codes** in §5.2, §5.6, and §5.8 now match the actual role enforcement and `ErrorCode` enum in `main.py`.
7. **Post-DEPLOY-002 work** (live HTTP client wiring to downstream services) should be tracked as a follow-on task once the compose stack is stable.

---

*End of DEPLOY-002-SIDECAR-BFF-HANDOFF.md (v2)*

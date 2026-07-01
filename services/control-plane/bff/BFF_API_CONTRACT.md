# BFF API Contract (v1)

Last updated: 2026-06-15
Status: canonical — governed BFF read API contract for APP-001
Tier: L2 Planning & Execution (formal API contract derived from L1 policy)
Scope: read API routes, request/response shapes, error contract, staleness model, RBAC matrix, composed views, and real-time feed contract for the governed BFF
Owner: Qwen
Reviewer: Codex
Derived from: PERSONA_RUNTIME_MODEL.md, BINDING_AND_DEPLOYMENT_SEMANTICS.md, BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md, TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md, LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md, EVOLUTION_REVIEW_AND_THRESHOLDS.md, ROLLBACK_AND_POSITION_SEMANTICS.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md, TARGET_ARCHITECTURE.md
Companion command contract: BFF_COMMAND_API_CONTRACT.md

---

## 1. Purpose

This document defines the **formal read API contract** for the governed BFF (Backend-for-Frontend) in APP-001.

It establishes:
- All read API routes with request/response shapes
- RBAC matrix mapping each surface to required roles
- Error contract and staleness model
- Composed view specifications for operator journeys
- Real-time feed contract (SSE transport)
- Versioning and deprecation policy

**Design rule**: The BFF is **read-oriented**. It must never create, modify, or delete canonical state. Every field returned by the BFF traces back to a canonical L1 object or a documented derived read-model. The BFF does not invent parallel truth sources.

Runtime, deployment, approval, incident, rollback, kill-switch, and evolution
commands are governed separately by `BFF_COMMAND_API_CONTRACT.md`. They must not
be folded into the GET read surfaces defined here.

---

## 2. Architectural Principles

These principles come directly from L1 canonical policy and are non-negotiable:

1. **No parallel truth** — Every field displayed by the BFF must trace back to a canonical L1 object (BFF_HA §4, BFF_SURFACE_INVENTORY.md §1).
2. **Control-plane isolation** — BFF failure must not affect active runtimes (BFF_HA §2.2).
3. **Partial degradation** — If a downstream service is unavailable, only the affected surface degrades (BFF_HA §5.1).
4. **Secondary control path** — High-privilege operators have a non-BFF path for safety-critical operations (BFF_HA §6).
5. **Stateless operation** — The BFF must not store canonical state locally (BFF_HA §3.1).
6. **Read/command split** — Read surfaces are GET-only; command admission uses the separate governed command facade and requires actor, trace, idempotency, policy/RBAC, and audit controls.

---

## 3. API Versioning

### 3.1 Version Format

URL-based versioning: `/api/v{version}/...`

Current version: **v1**

### 3.2 Versioning Policy

| Change Type | Version Impact |
|---|---|
| Add optional field to response | No version bump |
| Add new surface | No version bump |
| Add optional query parameter | No version bump |
| Remove field from response | Major version bump |
| Change field type | Major version bump |
| Change required field to optional | No version bump |
| Change authentication model | Major version bump |

### 3.3 Deprecation Policy

- Deprecated API versions receive a `Deprecation` response header
- Minimum 90-day notice before version removal
- Version transitions must align with canonical object evolution process (EVOLUTION_REVIEW_AND_THRESHOLDS.md)

---

## 4. Standard Query Envelope

### 4.1 List Endpoint

```
GET /api/v1/{resource}
  ?page=<int>                  # default: 1, min: 1
  &page_size=<int>             # default: 20, min: 1, max: 100
  &sort=<field>                # default: resource-specific primary key
  &sort_dir=<asc|desc>         # default: asc
  &filter.<field>=<value>      # resource-specific filterable fields
  &filter.<field>.op=<eq|ne|gt|gte|lt|lte|in|contains>  # default: eq
```

### 4.2 Filter Operators

| Operator | Applies To | Description |
|---|---|---|
| `eq` | All types | Exact match (default) |
| `ne` | All types | Not equal |
| `gt`, `gte`, `lt`, `lte` | Numeric, date | Comparison |
| `in` | All types | Comma-separated list |
| `contains` | String | Substring match (case-insensitive) |

Fields not on the per-surface filterable allowlist return HTTP 400 with `UNKNOWN_FILTER_FIELD`.

### 4.3 Pagination Limits

| Parameter | Default | Min | Max |
|---|---|---|---|
| `page` | 1 | 1 | — |
| `page_size` | 20 | 1 | 100 |
| `depth` (LN-03) | 3 | 1 | 10 |
| Time range (TL-01) | — | — | 30 days max window |

---

## 5. Standard Response Envelope

### 5.1 List Response

```json
{
  "data": [
    { "id": "...", "type": "...", "...canonical fields...": "", "_links": {} },
    { "id": "...", "type": "...", "...canonical fields...": "", "_links": {} }
  ],
  "meta": {
    "total": 142,
    "page": 1,
    "page_size": 20,
    "total_pages": 8,
    "staleness": null
  }
}
```

### 5.2 Detail Response

```json
{
  "data": {
    "id": "...",
    "type": "...",
    "...canonical fields...": ""
  },
  "meta": {
    "staleness": null
  }
}
```

### 5.3 Resource Object Shape

Each resource object follows HATEOAS principles:

```json
{
  "id": "<resource_id>",
  "type": "<resource_type>",
  "... canonical fields from L1 object ...": "",
  "_links": {
    "self": "/api/v1/{resource}/{id}",
    "related": { "... related resource links ..." }
  }
}
```

### 5.4 Composed View Response

```json
{
  "data": {
    "deployment_plan": { "...": "..." },
    "capital_pool": { "...": "..." },
    "bindings": [],
    "runtime_binding": { "...": "..." },
    "rollbacks": []
  },
  "meta": {
    "snapshot_at": "2026-04-10T03:00:00Z",
    "surfaces": {
      "deployment_plan": { "status": "ok" },
      "capital_pool": { "status": "ok" },
      "bindings": { "status": "ok" },
      "runtime_binding": {
        "status": "degraded",
        "staleness": {
          "served_from": "cache",
          "last_known_at": "2026-04-10T02:30:00Z"
        }
      },
      "rollbacks": { "status": "unavailable" }
    }
  }
}
```

---

## 6. Error Response Contract

### 6.1 Standard Error Shape

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<Human-readable message>",
    "details": {}
  }
}
```

### 6.2 Error Code Catalog

| Error Code | HTTP Status | Description |
|---|---|---|
| `INVALID_REQUEST` | 400 | Request body or query parameter validation failed |
| `UNKNOWN_FILTER_FIELD` | 400 | Filter field not in allowlist for this surface |
| `INVALID_FILTER_VALUE` | 400 | Filter value does not match expected type |
| `INVALID_TIME_RANGE` | 422 | Time range exceeds maximum window or is inverted |
| `PAGINATION_OUT_OF_RANGE` | 422 | Page number or page_size outside allowed bounds |
| `UNAUTHORIZED` | 401 | Authentication required or token invalid |
| `FORBIDDEN` | 403 | Operator lacks required role for this surface |
| `NOT_FOUND` | 404 | Resource ID does not exist |
| `DOWNSTREAM_UNAVAILABLE` | 503 | Downstream service unreachable |
| `DOWNSTREAM_TIMEOUT` | 504 | Downstream service request timed out |
| `INTERNAL_ERROR` | 500 | Unexpected BFF internal error |

### 6.3 Degradation Is Not an Error

When a surface returns degraded data (stale, partial, or replica-backed), the BFF returns **HTTP 200** with appropriate `meta.staleness` metadata — **not** an error response. This allows the UI to render partial pages.

Error responses (503/504) are only returned when:
- A single-surface endpoint has **no verifiable payload** because the downstream service is unavailable
- The client request itself is invalid (4xx)

**"Never show none" rule**: The BFF must never return `"data": []` or `"data": null` as a result of a downstream service failure. List surfaces return last-known or replica-backed entries with staleness metadata when available; otherwise they return HTTP 503 with `DOWNSTREAM_UNAVAILABLE`. Detail surfaces return HTTP 503 if the specific resource cannot be verified.

---

## 7. Staleness and Degradation Model

### 7.1 Staleness States

| State | `served_from` Value | Meaning |
|---|---|---|
| Fresh (null) | — | Fresh data from primary service |
| `degraded` | `"read-replica"` | Data served from read-replica |
| `stale` | `"cache"` | Data served from cache with known staleness |
| `partial` | `"reconstructed"` | Data reconstructed from direct object references |
| `unavailable` | — | No data available |

### 7.2 Surface-Level Staleness (v1 Baseline)

```json
{
  "meta": {
    "staleness": {
      "served_from": "cache",
      "last_known_at": "2026-04-10T02:30:00Z"
    }
  }
}
```

### 7.3 Degradation Behavior by Surface Group

| Surface Group | Fallback Strategy | Degradation Indicator |
|---|---|---|
| IN-01–IN-05, RT-01–RT-04, CP-03–CP-04 | Last-known state from cache | `"unverifiable"` (HTTP 503 if no cache) |
| DP-01–DP-04, EV-01–EV-04, LN-01–LN-03 | Read-replica → reconstructed → unavailable | `"stale"` / `"partial"` / `"unverifiable"` |
| TL-01–TL-03, PS-01–PS-06, CP-01–CP-02 | Read-replica with performance note | `"degraded"` |

### 7.4 Field-Level Staleness (Target State)

Deferred to v2. The v1 baseline uses surface-level staleness only.

---

## 8. RBAC Matrix

### 8.1 Role Model

The BFF reuses roles defined in the P4-001 control-plane routing contract with read-scope extensions:

| Role | Base Permission | Scope |
|---|---|---|
| `viewer` | Read-only on non-sensitive surfaces | Global or pool-scoped |
| `operator` | Read on all surfaces | Global or pool-scoped |
| `approver` | Read on approval/deployment-adjacent surfaces | Pool-scoped |
| `admin` | Read + can access secondary control path guidance surfaces | Global |

### 8.2 Surface-to-Role Mapping

| Surface Group | Minimum Role | Scope | Notes |
|---|---|---|---|
| PS-01 (Persona List) | `viewer` | Global or pool-scoped | Lifecycle state visible to all |
| PS-02–PS-06 | `operator` | Pool-scoped | Session and teaching data restricted |
| CP-01–CP-04 | `operator` | Pool-scoped | Capital pool binding data |
| DP-01–DP-04 | `operator` | Pool-scoped | Deployment plans and approvals |
| RT-01–RT-04 | `operator` | Pool-scoped | Runtime binding state |
| TL-01–TL-03 | `viewer` | Pool-scoped | Telemetry is read-only informational |
| LN-01–LN-03 | `viewer` | Global | Lineage is read-only audit data |
| IN-01–IN-05 | `operator` | Pool-scoped | Incident data is safety-critical |
| EV-01–EV-04 | `operator` | Pool-scoped | Evolution decisions are governance records |
| Composed views | `operator` | Pool-scoped | All composed views require operator role |
| Kill-switch status (IN-05) | `admin` | Global | Safety-critical; restricted access |

### 8.3 RBAC Evaluation

RBAC is evaluated by the **API Gateway Layer** (BFF_HA §4 — auth / RBAC facade) before routing to the composition layer. Requests that fail RBAC return HTTP 403 `FORBIDDEN`.

Degraded access policy: When a surface is in degraded mode, the RBAC check still applies — degradation does not relax authorization. However, surfaces that are normally `viewer`-accessible may be further restricted to `operator` during degraded mode if the downstream service cannot verify pool-scoped access.

---

## 9. API Routes

### 9.1 Persona Surfaces (PS-01–PS-06)

**Canonical source**: PERSONA_RUNTIME_MODEL.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/personas` | GET | PS-01 | `{ data: [Persona], meta }` | `lifecycle_state`, `mandate`, `strategy_family` |
| `/api/v1/personas/{persona_id}` | GET | PS-02 | `{ data: Persona + bindings[], meta }` | — |
| `/api/v1/personas/{persona_id}/sessions` | GET | PS-03 | `{ data: [SessionPersona], meta }` | `status` |
| `/api/v1/sessions/{session_id}` | GET | PS-04 | `{ data: SessionPersona + CapabilitySnapshot, meta }` | — |
| `/api/v1/personas/{persona_id}/teaching` | GET | PS-05 | `{ data: [TeachingSession], meta }` | `status` |
| `/api/v1/personas/{persona_id}/capabilities` | GET | PS-06 | `{ data: CapabilitySnapshot, meta }` | — |

### 9.2 Capital Pool & Binding Surfaces (CP-01–CP-04)

**Canonical source**: BINDING_AND_DEPLOYMENT_SEMANTICS.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/capital-pools` | GET | CP-01 | `{ data: [CapitalPool], meta }` | `status`, `risk_policy_ref` |
| `/api/v1/capital-pools/{pool_id}` | GET | CP-02 | `{ data: CapitalPool + bindings[], meta }` | — |
| `/api/v1/bindings` | GET | CP-03 | `{ data: [PersonaCapitalBinding], meta }` | `persona_id`, `capital_pool_id`, `role`, `validity` |
| `/api/v1/bindings/{binding_id}` | GET | CP-04 | `{ data: PersonaCapitalBinding + Persona, meta }` | — |

### 9.3 Deployment Surfaces (DP-01–DP-04)

**Canonical source**: BINDING_AND_DEPLOYMENT_SEMANTICS.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/deployment-plans` | GET | DP-01 | `{ data: [DeploymentPlan], meta }` | `status`, `capital_pool_id` |
| `/api/v1/deployment-plans/{plan_id}` | GET | DP-02 | `{ data: DeploymentPlan + ApprovalDecision, meta }` | — |
| `/api/v1/approval-decisions` | GET | DP-03 | `{ data: [ApprovalDecision], meta }` | `outcome`, `state` |
| `/api/v1/approval-decisions/{decision_id}` | GET | DP-04 | `{ data: ApprovalDecision, meta }` | — |

### 9.4 Runtime Surfaces (RT-01–RT-04)

**Canonical sources**: BINDING_AND_DEPLOYMENT_SEMANTICS.md, ROLLBACK_AND_POSITION_SEMANTICS.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/runtime-bindings` | GET | RT-01 | `{ data: [RuntimeBinding], meta }` | `deployment_mode`, `version` |
| `/api/v1/runtime-bindings/{binding_id}` | GET | RT-02 | `{ data: RuntimeBinding + DeploymentPlan, meta }` | — |
| `/api/v1/runtimes/{runtime_id}/status` | GET | RT-03 | `{ data: RuntimeBinding, meta }` | — |
| `/api/v1/runtimes/{runtime_id}/rollbacks` | GET | RT-04 | `{ data: [RollbackRecord], meta }` | `action_type`, `time_range` |

### 9.5 Telemetry Surfaces (TL-01–TL-03)

**Canonical source**: TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/telemetry` | GET | TL-01 | `{ data: [TelemetryEvent], meta }` | `pool_id`, `artifact_id`, `time_range` |
| `/api/v1/telemetry/{runtime_id}/summary` | GET | TL-02 | `{ data: TelemetrySummary, meta }` | `time_range`, `aggregate_by` |
| `/api/v1/telemetry/{artifact_id}/performance` | GET | TL-03 | `{ data: PerformanceChart, meta }` | `time_range` |

Time range parameters must be valid RFC 3339 timestamps. Inverted ranges (start > end) return HTTP 422 `INVALID_TIME_RANGE`. Windows exceeding 30 days return HTTP 422.

### 9.6 Lineage Surfaces (LN-01–LN-03)

**Canonical source**: LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/lineage` | GET | LN-01 | `{ items: [{ artifact_id, edge_count, last_edge_at }], page_info: { next_page_token }, meta }` | `artifact_id`, `page_token`, `page_size` |
| `/api/v1/lineage/edges/{edge_id}` | GET | LN-02 | `{ id, from_artifact_id, to_artifact_id, relationship, created_at, meta }` | — |
| `/api/v1/lineage/graph` | GET | LN-03 | `{ nodes: [{ artifact_id, artifact_version, artifact_type }], edges: [{ id, from_artifact_id, to_artifact_id, relationship }], meta }` | `root_type`, `root_id`, `depth` |

### 9.7 Incident Surfaces (IN-01–IN-05)

**Canonical sources**: TARGET_ARCHITECTURE.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/incidents` | GET | IN-01 | `{ data: [IncidentCase], meta }` | `status`, `severity`, `affected_pool_id` |
| `/api/v1/incidents/{incident_id}` | GET | IN-02 | `{ data: IncidentCase, meta }` | — |
| `/api/v1/postmortems` | GET | IN-03 | `{ data: [PostmortemReport], meta }` | `time_range` |
| `/api/v1/postmortems/{report_id}` | GET | IN-04 | `{ data: PostmortemReport, meta }` | — |
| `/api/v1/kill-switch/status` | GET | IN-05 | `{ data: { active_freeze_orders: [FreezeOrder], affected_runtime_bindings: [RuntimeBinding] }, meta }` | — |

### 9.8 Evolution Surfaces (EV-01–EV-04)

**Canonical sources**: EVOLUTION_REVIEW_AND_THRESHOLDS.md, ROLLBACK_AND_POSITION_SEMANTICS.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/evolution-decisions` | GET | EV-01 | `{ items: [EvolutionDecision], page_info: { next_page_token }, meta: { snapshot_at } }` | `action_type`, `risk_level`, `status`, `page_token`, `page_size` |
| `/api/v1/evolution-decisions/{decision_id}` | GET | EV-02 | `EvolutionDecision` fields at the response root plus `meta: { snapshot_at }` | — |
| `/api/v1/freeze-orders` | GET | EV-03 | `{ items: [FreezeOrder], meta: { snapshot_at } }` | `status`, `scope` |
| `/api/v1/rollbacks` | GET | EV-04 | `{ items: [RollbackRecord], meta: { snapshot_at } }` | `runtime_id`, `action_type`, `time_range` |

### 9.9 AlphaFactory Board (AF-01)

**Canonical source**: Research-to-strategy pipeline read-model.
**Consumer**: `FE AlphaFactoryBoard` (idea→strategy kanban view).
**Implementation**: `console_gap/alpha_factory.py` — registered via `app.include_router` in `main.py`.

| Route | Method | Surface ID | Min Role | Response |
|---|---|---|---|---|
| `/bff/alpha-factory` | GET | AF-01 | `operator` | Canonical list envelope — see below |

**Request parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `lane` | string | — | Filter cards to one lane: `ideas`, `strategies`, or `experiments` |
| `page` | int | 1 | Page number (min 1) |
| `page_size` | int | 20 | Items per page (min 1, max 100) |

**Happy-path response** (HTTP 200):

```json
{
  "data": {
    "id": "alpha-factory",
    "snapshotAt": "<RFC3339>",
    "snapshot_at": "<RFC3339>",
    "lanes": [
      { "id": "ideas",       "label": "Ideas" },
      { "id": "strategies",  "label": "Strategies" },
      { "id": "experiments", "label": "Experiments" }
    ],
    "items": [ { "id": "card-001", "lane": "ideas", "title": "...", "status": "..." } ]
  },
  "items": [ { "...": "..." } ],
  "page_info": { "page": 1, "page_size": 20, "total": 1, "next_page_token": null },
  "meta": {
    "snapshot_at": "<RFC3339>",
    "surfaces": { "alpha_factory": { "status": "ok", "source": "service_store" } },
    "filters": { "lane": null }
  }
}
```

**Degraded response** — store unavailable (HTTP 200, never bare `[]`):

```json
{
  "data": { "id": "alpha-factory", "lanes": [...], "items": [] },
  "items": [],
  "page_info": { "page": 1, "page_size": 20, "total": 0, "next_page_token": null },
  "meta": {
    "snapshot_at": "<RFC3339>",
    "surfaces": {
      "alpha_factory": {
        "status": "unavailable",
        "source": "missing",
        "staleness": { "served_from": "unverifiable", "last_known_at": "<RFC3339>" }
      }
    },
    "filters": { "lane": null }
  }
}
```

**Design notes**:
- The "never show none" rule applies: the BFF never returns bare `[]` due to a downstream failure.  When the backing dataset is missing the surface status is `unavailable` and `items` is an explicit empty list with degradation metadata.
- Auth/CORS re-uses the existing BFF token-stub and CORS middleware; no new middleware needed.
- The backing store method is `ReadSurfaceStore.list_alpha_factory_cards(page, page_size, lane)`.

### 9.10 Automation Registry BFF Surfaces (AR-01–AR-02)

**Canonical sources**: LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md, BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md, services/control-plane/cron workflow registry, OpenClaw adapter workflow policy

These `/bff/*` surfaces are read-only console aliases for frontend automation
registry screens. They expose registered workflow templates and cron/hook
entries only; they do not trigger, schedule, approve, or mutate workflow state.

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/bff/workflows` | GET | AR-01 | `{ data: [WorkflowTemplate], items: [WorkflowTemplate], page_info: { next_page_token, total, returned }, meta: { snapshot_at, surfaces.workflow_templates } }` | `page_token`, `page_size` |
| `/bff/hooks` | GET | AR-02 | `{ data: [HookRegistryEntry], items: [HookRegistryEntry], page_info: { next_page_token, total, returned }, meta: { snapshot_at, surfaces.hook_registry } }` | `page_token`, `page_size` |

If the backing automation registry store is absent, these endpoints still return
HTTP 200 with the canonical list envelope and empty `data`/`items`; they must not
return a bare `[]`. The unavailable condition is explicit in
`meta.surfaces.<surface_key>`:

```json
{
  "status": "unavailable",
  "source": "missing"
}
```
---

## 10. Composed Views

### 10.1 Composed View Endpoints

| Route | Composes | Primary Use Case | Min Role |
|---|---|---|---|
| `/api/v1/operator/runtime-state` | RT-03, RT-04, TL-02 | Multi-runtime operator roster with telemetry and rollback summaries | `operator` |
| `/api/v1/operator/health-status` | RT-01, TL-02, IN-01, governance review + approval queues, IN-05 | Operator health board with safe-mode state and fallback guidance | `operator` |
| `/api/v1/operator/alerts` | IN-01, governance review + approval queues, IN-05, RT-01, TL-02 | Operator alert rail with backend-owned severity, category, and target refs | `operator` |
| `/api/v1/operator/home` | OC-02, OC-03, OC-04 summaries plus IN-05 safe-mode state | Operator home dashboard with card hierarchy and escalation shortcuts | `operator` |
| `/api/v1/operator/paper-live-drift/{runtime_id}` | drift report, RT-01, TL-02, TL-03, approval decision, incidents, evolution evidence | Paper-vs-live drift review with backend-owned threshold evaluation and follow-up actions | `operator` |
| `/api/v1/operator/research/oss-activation-ready` | Research orchestrator, policy-learning, research-worker gateway, and OpenClaw adapter capability/activity metadata | Read-only OSS activation-ready operations view: capability, gate state, run history, artifact refs, logs, and error summaries; no activation, registry, governance, broker, or capital-binding write path | `operator` |
| `/api/v1/operator/research/oss-preactivation` | Alias for `/api/v1/operator/research/oss-activation-ready` | Backward-compatible pre-activation route name; same read-only non-bypass contract | `operator` |
| `/api/v1/operator/openclaw/ops` | OpenClaw adapter upstream status, Pantheon-owned lifecycle sessions, tool/workflow policy, and invocation audit | OpenClaw operator operations surface showing upstream reachability, degraded reasons, session lifecycle state, paper/live gate state, and bridge audit without enabling broker, paper, live, or capital-binding paths | `operator` |
| `/api/v1/operator/openclaw/tool-workflow-bridge` | Alias for `/api/v1/operator/openclaw/ops` focused on bridge-oriented UI adoption | Backward-compatible bridge handoff route name; same read-only OpenClaw operations projection and fail-closed gate contract | `operator` |
| `/api/v1/workbench/consultation` | CW-008 packet-family truth only | Consultation Workbench overview surface; truthful module status without fake request or committee UI | `operator` |
| `/api/v1/workbench/knowledge` | KW-006 packet-family truth only | Knowledge Workbench overview surface; truthful module status without fake registry or evidence UI | `operator` |
| `/api/v1/operator/deployment-review/{plan_id}` | DP-02, CP-02, CP-04, RT-02, RT-04 | Pre-deployment approval review | `operator` |
| `/api/v1/operator/incident-response/{incident_id}` | IN-02, RT-03, TL-02, RT-04, EV-04, IN-05 | Active incident response | `operator` |
| `/api/v1/operator/post-incident-review/{incident_id}` | IN-04, EV-01, EV-02, LN-01, TL-03 | Post-incident analysis | `operator` |
| `/api/v1/operator/persona-management/{persona_id}` | PS-02, CP-03, CP-04, PS-03, PS-05 | Persona lifecycle management | `operator` |

### 10.1.1 Source-Reference BFF Aliases

| Route | Backing View | Response | Min Role |
|---|---|---|---|
| `/bff/alerts` | `/api/v1/operator/alerts` | `{ alerts, summary, meta }` with backend-owned severity, category, target refs, and surface degradation metadata | `operator` |
| `/bff/alerts/{id}` | `/api/v1/operator/alerts` | `{ data: AlertProjection, meta }` for a single projected alert id | `operator` |
| `/bff/knowledge` | Composed Knowledge inbox over `/api/v1/knowledge/notes`, `/api/v1/knowledge/evidence`, `/api/v1/knowledge/insights`, `/api/v1/knowledge/strategy-specs`, and `/api/v1/knowledge/memory` | `{ data, items, page_info, meta }`; when no backing knowledge store is readable, returns an empty `data/items` list with `meta.surfaces.knowledge_inbox.status: unavailable` and `source: missing` rather than a bare array | `operator` |

### 10.1.2 BFF Management Endpoints (BFFGAP-CONSOLE Series)

Endpoints in the `/bff/management/` namespace are BFF-only composite read views served
directly from the source-ingest service registry or aggregated read surfaces.
They follow the canonical list envelope (§5.1) and emit explicit degraded envelopes
when the backing store is unconfigured or unreachable (§7.2).

| Route | Composes | Response Envelope | Degraded Behavior | Min Role |
|---|---|---|---|---|
| `GET /bff/management/data-sources` | source-ingest `/api/source-ingest/registry` | `DataSourcesEnvelope`; `data: { id, items, status, source }`; typed `page_info`; typed `meta.surfaces.data_sources` | When source-ingest URL is unconfigured (`source:missing`) or unreachable (`source:unavailable`): empty `items`, `meta.status:unavailable`, `data.status:unavailable`, and surface `staleness` — never a bare `[]` | `operator` |
| `GET /bff/management/permissions` | governance permission read surface (`governance_permissions`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.governance_permissions` | When the dataset is missing: empty `data/items`, `meta.status:unavailable`, surface `source:missing`, and `meta.degradation.reason` | `operator` |
| `GET /bff/management/memory-governance` | memory governance read surface (`memory_governance_rules`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.memory_governance_rules` | When the dataset is missing: empty `data/items`, `meta.status:unavailable`, surface `source:missing`, and `meta.degradation.reason` | `operator` |
| `GET /bff/management/consult-rules` | consult rule read surface (`consult_rules`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.consult_rules` | When the dataset is missing: empty `data/items`, `meta.status:unavailable`, surface `source:missing`, and `meta.degradation.reason` | `operator` |
| `GET /bff/lineage` | lineage-edge store (`lineage_edges`) via `ReadSurfaceStore` | `LineageEnvelope`; `data: { id, nodes, edges, status, source }`; typed `page_info`; typed `meta.surfaces.lineage`; query params: `root_id`, `root_type`, `depth` (default 3, max 20), `artifact_id` | When lineage store is missing (`source:missing`): empty `nodes`/`edges`/`items`, `meta.status:unavailable`, `data.status:unavailable` — never a bare `[]` | `operator` |
| `GET /bff/workflows` | workflow template registry (`workflow_templates`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.workflow_templates` | When the registry is missing: empty `data/items`, `meta.degradation.reason`, surface `status:unavailable` | `operator` |
| `GET /bff/hooks` | hook registry (`hook_registry`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.hook_registry` | When the registry is missing: empty `data/items`, `meta.degradation.reason`, surface `status:unavailable` | `operator` |
| `GET /bff/knowledge` | composed knowledge inbox over notes, evidence, insights, strategy specs, and memory | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.knowledge_inbox` plus source surfaces and `composition_sources` | When all source datasets are missing: empty `data/items`, `knowledge_inbox.status:unavailable`, and per-source surface `source:missing` | `operator` |

**Degraded envelope example** (source-ingest unconfigured in dev):

```json
{
  "data": { "id": "management-data-sources", "items": [], "status": "unavailable", "source": "missing" },
  "items": [],
  "page_info": { "next_page_token": null, "total": 0, "page_size": 0, "returned": 0, "has_more": false },
  "meta": {
    "snapshot_at": "...",
    "status": "unavailable",
    "source": "missing",
    "surfaces": {
      "data_sources": {
        "status": "unavailable",
        "source": "missing",
        "message": "Source-ingest registry is unavailable or unconfigured.",
        "staleness": { "served_from": "unverifiable", "last_known_at": "..." }
      }
    },
    "degradation": { "reason": "management data sources are currently unavailable." }
  }
}
```

OpenAPI must publish these shared schema components for management console reads:
`SurfaceState`, `PageInfo`, `ManagementListMeta`, `ManagementRecordsEnvelope`,
`DataSourcesEnvelope`, and `LineageEnvelope`.

### 10.2 Consistency Model

Composed views support the `snapshot` query parameter:

```
GET /api/v1/operator/deployment-review/{plan_id}?snapshot=preferred
```

| Value | Behavior |
|---|---|
| `preferred` (default) | Attempt point-in-time consistency across all surfaces; fall back to best-effort with degradation metadata if surfaces fall outside the time window |
| `best_effort` | Accept data from different timestamps without attempting alignment |

When `snapshot=preferred`, the response includes `meta.snapshot_at` indicating the reference time. Surfaces fetched outside the narrow window carry a staleness marker relative to `snapshot_at`.

### 10.3 Partial Rendering

Each composed view returns `meta.surfaces` showing the status of each sub-surface:

```json
{
  "meta": {
    "surfaces": {
      "deployment_plan": { "status": "ok" },
      "runtime_binding": {
        "status": "error",
        "error": {
          "code": "DOWNSTREAM_TIMEOUT",
          "message": "Runtime manager did not respond within 5s"
        }
      }
    }
  }
}
```

---

## 11. Real-Time Feed Contract

### 11.1 Transport Mechanism

**Server-Sent Events (SSE)** for v1. Rationale:
- BFF real-time feeds are server→client only (state changes, alerts, updates)
- SSE is simpler to implement, HTTP-native, automatic reconnection
- Works through standard load balancers (aligns with BFF_HA §3.1 stateless architecture)
- WebSocket can be added in v2 if bidirectional communication is needed

### 11.2 Subscription Endpoints

| SSE Endpoint | Data Stream | Reconnection |
|---|---|---|
| `GET /api/v1/runtime/{runtime_id}/events/stream` | Runtime state changes | `?last_event_id=` |
| `GET /api/v1/incidents/stream` | Active incident events (Legacy) | `?last_event_id=` |
| `GET /api/v1/kill-switch/updates` | Kill-switch state changes | `?last_event_id=` |
| `GET /api/v1/approvals/stream` | Approval lifecycle events | `?last_event_id=` |
| `GET /api/v1/agora/ask/stream` | Ask session events | `?last_event_id=` |
| `GET /api/v1/stream/{channel}` | Generic stream for any catalog channel | `?last_event_id=` |

**Channel Catalog**:
`approval, ask, artifact, runtime, mcp, skill, channel, tool, ranking, rebalance, evolution, research, signal, inbox, journal, postmortem, loop, sentinel, intervention, audit, system`

### 11.3 Stream Event Shape

```json
{
  "id": "evt-20260410T030000Z-abc123",
  "type": "runtime_state_changed",
  "timestamp": "2026-04-10T03:00:00Z",
  "data": {
    "runtime_id": "r-001",
    "previous_state": "paper",
    "current_state": "canary",
    "surface_id": "RT-03"
  }
}
```

**New Event Types**:

- **Approval**:
  - `approval.created`: New approval request submitted
  - `approval.stage.changed`: Approval moved to next review stage
  - `approval.decided`: Approval reached terminal state (approved/rejected)
  - `approval.sla.escalated`: Approval breached SLA threshold

- **Ask**:
  - `ask.session.started`: New Agora ask session initialized
  - `ask.message.delta`: Real-time token streaming for ask responses
  - `ask.tool.called`: Persona calling a tool/skill
  - `ask.message.completed`: Full message content delivered
  - `ask.session.completed`: Session finished successfully
  - `ask.session.failed`: Session terminated with error

### 11.4 Replay and Resync

BFF maintains an in-memory buffer of the last 500 events per channel.

- If `last_event_id` is found in the buffer, BFF replays all events after that ID before streaming new events.
- If `last_event_id` is provided but **not found** in the buffer (due to buffer rotation or server restart), BFF returns **HTTP 409** with `SSE_REPLAY_UNAVAILABLE`.
- In case of 409, the client **must** resync full canonical state via the channel-specific resync routes (advertised in `X-SSE-Resync-Routes`) before reconnecting.

**Channel-specific resync routes**:

| Channel | Resync Routes |
|---|---|
| `approval` | `GET /bff/approvals`, `GET /bff/v5/interventions` |
| `ask` | `GET /bff/agora/ask/sessions/{id}`, `GET /bff/agora/committee/sessions/{id}` |
| All others | No canonical resync route; reconnect with `last_event_id=` omitted |

**SSE_REPLAY_UNAVAILABLE error shape** (HTTP 409):
```json
{
  "error": {
    "code": "SSE_REPLAY_UNAVAILABLE",
    "message": "Event ID <id> is no longer in the buffer",
    "details": {
      "reason": "SSE_REPLAY_HISTORY_MISSING",
      "channel": "<channel>",
      "lastEventId": "<id>",
      "replaySupported": true,
      "replayWindowEvents": 500,
      "replayStore": "in-memory",
      "resyncRoutes": ["<resync-route>", ...]
    }
  }
}
```

**BFF HA note**: replay store is single-replica in-memory only. Multi-replica shared replay store is out of scope.

### 11.5 SSE Headers

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
X-SSE-Channel: <channel>
X-SSE-Replay-Supported: true
X-SSE-Replay-Window-Events: 500
X-SSE-Buffer-Size: 500
X-SSE-Replay-Store: in-memory
X-SSE-Resync-Routes: <comma-separated resync routes>   (approval and ask channels only)
```

---

## 12. Secondary Control Path

Per BFF_HA §6, the following operations **must** have a non-BFF path:

| Operation | Secondary Path | Minimum RBAC |
|---|---|---|
| Kill-switch activation | Admin CLI / runtime-manager protected admin endpoint | `admin` (global) |
| Runtime rollback | Admin CLI / runtime-manager admin endpoint | `admin` (global) |
| Runtime pause | Admin CLI / runtime-manager admin endpoint | `admin` (global) |
| Health diagnostics | Control-plane internal API | `admin` (global) |

The BFF **documents** these paths in its API contract but **does not implement** them. Implementation belongs to APP-002 (operator surfaces) and the runtime-manager team.

The BFF exposes `GET /api/v1/kill-switch/status` (IN-05) as a read-only status check — this is **not** an execution path.

---

## 13. BFF Design Rule: Read-Only Guarantee

The BFF read API contract enforces a strict **read-only guarantee**:

1. All endpoints listed in this read contract are **GET** only (no POST/PUT/PATCH/DELETE on the read API surface)
2. This contract covers the **read-oriented APP-001 surface only**. Runtime/deployment/approval/incident command paths are documented separately in `BFF_COMMAND_API_CONTRACT.md` and must not reuse these GET surfaces as a pseudo-write channel.
3. The BFF does not maintain its own canonical state
4. Every response field traces back to a canonical L1 object or a documented derived read-model

This ensures the BFF remains on the control/UI plane and never becomes a source of truth parallel to the canonical L1 policy objects.

---

## 14. Governance Sub-Rules Read Endpoints (BFFGAP-GOVRULES, P1)

These four read-only list endpoints serve the FE governance/{permissions,memory,consult,policies} pages.
All use the canonical list envelope and degrade gracefully when the backing store is unavailable.

### 14.1 Response Envelope

All four endpoints share the same canonical list envelope:

```json
{
  "data": [...],
  "items": [...],
  "page_info": {
    "next_page_token": null | "<opaque token>",
    "total": <int>,
    "page_size": <int>
  },
  "meta": {
    "snapshot_at": "<ISO-8601Z>",
    "surfaces": {
      "<surface_key>": { "status": "ok" | "degraded" | "unavailable", "source": "<string>" }
    }
  }
}
```

When the backing store is absent (`source: missing`), the surface status is `unavailable` and `items`/`data` are empty arrays — never a bare `[]` at the top level.

### 14.2 Endpoint Table

| ID | Route | RBAC | Dataset key | Surface key | FE page |
|---|---|---|---|---|---|
| GR-01 | `GET /bff/management/permissions` | `viewer` | `governance_permissions` | `governance_permissions` | governance/permissions |
| GR-02 | `GET /bff/management/memory-governance` | `viewer` | `memory_governance_rules` | `memory_governance_rules` | governance/memory |
| GR-03 | `GET /bff/management/consult-rules` | `viewer` | `consult_rules` | `consult_rules` | governance/consult |
| GR-04 | `GET /bff/route-policies` | `viewer` | `route_policies` | `route_policies` | governance/policies |

### 14.3 Query Parameters (all four endpoints)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page_size` | `int` | 50 | Items per page (1–200) |
| `page_token` | `string` | — | Opaque offset token from previous `next_page_token` |

### 14.4 Auth & CORS

Reuses the same `Authorization: Bearer <token>` pattern and CORS configuration as all other BFF read endpoints.
Minimum required role: `viewer` (same as `_require_read_role`).

### 14.5 Implementation Note

Routes are implemented in `services/control-plane/bff/console_gap/{permissions,memory_governance,consult_rules,route_policies}.py`
and registered in `main.py` via `_include_governance_subrules_routes()` + `include_router()`.
New `list_*` methods were added to `ReadSurfaceStore` in `read_store.py`.

---

## 15. Surface Count Summary

| Domain | Surface IDs | Count |
|---|---|---|
| Persona (PS) | PS-01 to PS-06 | 6 |
| Capital Pool & Binding (CP) | CP-01 to CP-04 | 4 |
| Deployment (DP) | DP-01 to DP-04 | 4 |
| Runtime (RT) | RT-01 to RT-04 | 4 |
| Telemetry (TL) | TL-01 to TL-03 | 3 |
| Lineage (LN) | LN-01 to LN-03 | 3 |
| Incident (IN) | IN-01 to IN-05 | 5 |
| Evolution (EV) | EV-01 to EV-04 | 4 |
| AlphaFactory Board (AF) | AF-01 | 1 |
| **Canonical v1 Subtotal** | | **34** |
| Automation registry (AR) | AR-01 to AR-02 | 2 |
| Governance sub-rules (GR) | GR-01 to GR-04 | 4 |
| Composed views | 10 | 10 |
| SSE streams (runtime, incidents, kill-switch, approvals, ask, generic) | 6 | 6 |
| BFF Management (BFFGAP-CONSOLE additive surfaces) | DS-01 (`/bff/management/data-sources`), LIN-01 (`/bff/lineage`) | 2 |
| **Total v1 endpoints** | | **58** |

`MGMT-GAP-003` acceptance intentionally spans eight management-console read
routes: DS-01, GR-01, GR-02, GR-03, LIN-01, AR-01, AR-02, and KW-BFF-01.
GR/AR/KW routes remain counted in their owning rows above, so they are not
double-counted in the additive total.

---

## 16. Verification Checklist

| APP-001 Acceptance Criterion | Status | Evidence |
|---|---|---|
| BFF read contract is read-oriented | ✅ | §1 Purpose, §13 Read-Only Guarantee — all read-contract endpoints are GET-only, no canonical state writes |
| Runtime/deployment/approval/incident commands are split from read surfaces | ✅ | Companion `BFF_COMMAND_API_CONTRACT.md` defines the governed command facade with actor, trace, idempotency, RBAC/policy, and audit requirements |
| Consultation surfaces cite canonical objects | ✅ | See CONSULTATION_SURFACE_CONTRACT.md — all consultation surfaces reference L1 canonical objects with explicit object lineage |
| Degraded operator path is documented | ✅ | §7 Staleness and Degradation Model, §12 Secondary Control Path, BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5-§6 |
| Governance sub-rules endpoints degrade safely | ✅ | §14 — empty/missing store returns explicit `status:unavailable, source:missing` envelope; 14 contract tests in `tests/test_bff_governance_subrules_contract.py` |

---

*End of BFF API Contract (v1)*

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

#### 9.2.1 Governed quarterly paper allocation cycle

Paper personas compete for paper capital once per quarter through the existing ranking and allocation
path. Real capital allocation and its human approval path are unchanged; this mode can never create or
apply a real (canary/live) rebalance. No new BFF route: it rides the existing evaluation route.

| Route | Method | Role | Response |
|---|---|---|---|
| `/bff/management/allocation-policy/evaluate` with `authority_mode=governed_paper_quarterly_cycle` | POST | operator | `{ data: { cycle, rebalance, applied, replayed, receipt }, meta }` |
| `/bff/rebalances`, `/bff/rebalances/{rebalance_id}` | GET | read | the cycle proposal with its lines, cap reasons, exclusions, factors, `simulation`, `constraints`, `rollback_target` and apply receipt |

* Body: `authority_mode`, `quarter` (`YYYY-Qn`), `ranking_snapshot_id` (admitted quarterly snapshot of that
  quarter), `capital_pool_id` (paper pool, `metadata.execution_context=paper`), optional `pool_health` (per
  persona `risk_budget_factor` / `capacity_factor` / provenance). Without that `authority_mode` the route
  stays a read-only evaluation.
* The Capital owner recomputes targets from the snapshot with policy `persona-paper-quarterly-allocation-v1`
  (real tier caps S 25 % / A 15 % / B 8 %, +25 % quarterly increase cap, existing exclusions, total weight
  never above 1.0), creates one proposal per tenant + quarter + pool and applies it automatically in the
  paper ledger with an audit receipt and a rollback target. A rerun or catch-up returns the existing cycle.
* Pool-health factors default to 1.0; the reason is stored per persona line (`pool_health_factors`).
* A persona without a paper ledger on its ranking item or without exactly one active paper-owner binding is
  skipped with a recorded reason (`simulation.skipped`); an unknown snapshot, period mismatch, non-paper pool,
  non-paper environment or a caller without the operator role fails closed (4xx) and creates nothing.

### 9.3 Deployment Surfaces (DP-01–DP-04)

**Canonical source**: BINDING_AND_DEPLOYMENT_SEMANTICS.md

`GET /api/v1/deployment-plans` has been removed (no route; the framework returns `405`); `GET /bff/deployments` is the active deployment-plan list surface. Deployment-plan creation through `POST /api/v1/deployment-plans` and `POST /bff/deployments` is retired and returns `410 ACTION_RETIRED`. The governed replacement is approval, then the deployment owner `POST /api/deployment/plans/validate` and `POST /api/deployment/plans` endpoints. BFF does not create or validate deployment plans. All requests rejected through the existing retired-command mechanism likewise use the client-visible error code `ACTION_RETIRED` (HTTP 410), not `VALIDATION_FAILED`.

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/deployment-plans` | GET | Retired (DP-01) | Removed (`405`); use `GET /bff/deployments` | — |
| `/api/v1/deployment-plans` | POST | Retired | `410 ACTION_RETIRED`; use approval then deployment owner validate/create | — |
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
| `/api/v1/postmortems` | GET | IN-03 | `{ data: [PostmortemReport], page_info, meta }` (`meta.hidden_infrastructure_count`) | `time_range`, `status`, `q`, `include_infrastructure` |
| `/api/v1/postmortems/{report_id}` | GET | IN-04 | `{ data: PostmortemReport, meta }` | — |
| `/api/v1/kill-switch/status` | GET | IN-05 | `{ data: { active_freeze_orders: [FreezeOrder], affected_runtime_bindings: [RuntimeBinding] }, meta }` | — |

**IN-03 infrastructure drafts (POSTMORTEM-SKIP-INFRA-DRAFTS-20261125)**: draft postmortems of infrastructure-health and monitor-agent incidents are hidden by default; `include_infrastructure=true` passes through to the owner and shows them. `meta.hidden_infrastructure_count` reports how many were hidden (`null` when the surface is `unavailable`, or when the owner omits the field or returns a malformed/negative value: unknown is never reported as 0; `unavailable` stays distinct from an empty `ok` list). `total` and `page_info.total` count visible rows only and are exact. Reviewed, approved and published infrastructure postmortems are never hidden. The row shape is unchanged. Wrong tenant or role is refused as before.

### 9.8 Evolution Surfaces (EV-01–EV-04)

**Canonical sources**: EVOLUTION_REVIEW_AND_THRESHOLDS.md, ROLLBACK_AND_POSITION_SEMANTICS.md

| Route | Method | Surface | Response | Filterable Fields |
|---|---|---|---|---|
| `/api/v1/evolution-decisions` | GET | EV-01 | `{ items: [EvolutionDecision], page_info: { next_page_token }, meta: { snapshot_at } }` | `action_type`, `risk_level`, `status`, `page_token`, `page_size` |
| `/api/v1/evolution-decisions/{decision_id}` | GET | EV-02 | `EvolutionDecision` fields at the response root plus `meta: { snapshot_at }` | — |
| `/api/v1/freeze-orders` | GET | EV-03 | `{ items: [FreezeOrder], meta: { snapshot_at } }` | `status`, `scope` |
| `/api/v1/rollbacks` | GET | EV-04 | `{ items: [RollbackRecord], meta: { snapshot_at } }` | `runtime_id`, `action_type`, `time_range` |

### 9.9 AlphaFactory Board (AF-01)

**Canonical source**: `GET /api/research-orchestrator/alpha-replication/entries` (Research Orchestrator replication admission & queue join).
**Consumer**: `FE AlphaFactoryBoard` (replication admission & queue kanban view).
**Implementation**: `console_gap/alpha_factory.py` — registered via `app.include_router` in `core/app_factory.py`.

| Route | Method | Surface ID | Min Role | Response |
|---|---|---|---|---|
| `/bff/alpha-factory` | GET | AF-01 | `viewer` | Canonical list envelope — see below |

**Request parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `lane` | string | — | Filter items to one lane: `discovered`, `scaffolded`, `replicated`, or `document_checked` |
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
      { "id": "discovered", "label": "Discovered", "items": [ { "id": "dem-1", "kind": "research_demand", "display_name": "<persona>", "lacking": { "short_measure": "...", "missing_datasets": [] }, "since": "<RFC3339>", "latest_outcome": { "reason_code": "mandate_universe_missing" } } ] },
      { "id": "scaffolded", "label": "Scaffolded", "items": [] },
      { "id": "replicated", "label": "Replicated", "items": [ { "id": "adm-001", "lane": "replicated", "strategy_id": "...", "strategy_spec_id": "...", "status": "admitted", "queue_status": "completed" } ] }
    ],
    "discovered": [ { "...": "..." } ],
    "scaffolded": [],
    "replicated": [ { "...": "..." } ],
    "document_checked": [ { "id": "adm-002", "document_check_reason": "no_passing_gate_result" } ],
    "counts": { "collected_text_records": { "value": 7, "status": "ok" }, "dropped_irrelevant": { "value": null, "status": "unavailable", "reason": "owner_route_not_exposed" } },
    "items": [ { "id": "adm-001", "lane": "replicated", "strategy_id": "...", "strategy_spec_id": "...", "status": "admitted", "queue_status": "completed" } ]
  },
  "items": [ { "...": "..." } ],
  "page_info": { "page": 1, "page_size": 20, "total": 1, "next_page_token": null },
  "meta": {
    "snapshot_at": "<RFC3339>",
    "surfaces": { "alpha_factory": { "status": "ok", "source": "research_orchestrator" } },
    "filters": { "lane": null }
  }
}
```

**Degraded response** — owner unavailable or unconfigured (HTTP 200, never bare `[]`):

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
        "source": "research_orchestrator",
        "reason": "unconfigured",
        "staleness": { "served_from": "unverifiable", "last_known_at": "<RFC3339>" }
      }
    },
    "filters": { "lane": null }
  }
}
```

**Design notes**:
- The "never show none" rule applies: the BFF never returns bare `[]` due to a downstream failure. When the Research Orchestrator owner is unconfigured or fails, the surface status is `unavailable` and `items` is an explicit empty list with degradation metadata. A healthy owner with zero admissions returns `status: "ok"` with empty lanes.
- Auth/CORS re-uses the existing BFF token/JWT and CORS middleware; caller auth and verified tenant are forwarded to `GET /api/research-orchestrator/alpha-replication/entries`.
- Cards are built over HTTP from `GET /api/research-orchestrator/alpha-replication/entries` via `PANTHEON_RESEARCH_ORCHESTRATOR_API_URL`.
- BFF-EVO-ALPHA-PAGE-TRUTH-20261107: the owner route returns `last_error` from the queue record's `failure_reason` (null when absent) and passes `replay_count`, `last_replayed_at` and `revalidation_count` through exactly as the queue records them (null when absent). `attempt_count` means failures since the last replay, because a replay resets it to 0. Each card carries `last_error`, `replay_count`, `last_replayed_at`, `revalidation_count` and `strategy_state`.
- `strategy_state` is the current registry state of the card's `strategy_id` (for example `approved` or `retired`), read through the same registry strategy reader (`StrategiesService`, same read surface) as `GET /bff/strategies/{strategy_id}` (`StrategiesService.get_strategy_registry_state`), at most once per distinct `strategy_id` per request and never through a second registry client. A strategy the registry does not know has `strategy_state: null` with the surface `ok`. When a state read fails, `strategy_state` is `null` on the affected cards, `meta.surfaces.alpha_factory_strategy_state.status` is `unavailable`, and every card and lane is still returned. Lane rules are unchanged: no card is hidden or moved because of `strategy_state`, and the lane rules do not depend on `strategy_state` (the discovered lane no longer carries a hardcoded reason, see the next note). The route is a plain `def` handler and writes no owner state.
- BFF-ALPHA-FACTORY-PIPELINE-TRUTH-20261125 (RC6/RC2): the board reads the real pipeline. The hardcoded `no_candidate_source` reason (`data.reason`, `data.discovered_reason`, lane `reason`, `meta.discovered`) is removed; an empty lane means the owners answered with none, and a failed owner read is shown in `meta.surfaces`, never as an empty lane. Owner reads, at most four per request, all with the caller's credentials and verified tenant and none writing: replication entries, alpha-template evaluations, `GET /api/research/persona-demands` (the same research demand read as `GET /bff/research/tasks`; no second owner client), `GET /api/research-orchestrator/runs` (only runs stamped with the caller tenant), the run list is capped to the newest 200 rows of the caller tenant (`meta.surfaces.alpha_factory_runs.runs_seen`, `truncated`; the owner route has no limit parameter and returned about 570 KB on hosted on 2026-11-25, growing with every run). The board never requests `GET /api/source-ingest/source-records` or `GET /api/source-ingest/management/strategy-seeds` (unbounded: every tenant row); `hypothesis_cards`, `collected_text_records` and `dropped_irrelevant` are unavailable with reason `no_bounded_owner_read` until bounded owner reads exist.
  - `discovered`: open demands (`kind: research_demand`; `display_name` from the persona directory, `lacking` = `short_measure`, `missing_datasets`, `tried_families`; `since`; `latest_outcome.reason_code`) and hypothesis cards (`kind: hypothesis_card`; `claim`, `source_id`, `since`, `status`). A demand whose tenant differs from the caller marks `meta.surfaces.alpha_factory_demands` unavailable (`demand_scope_mismatch`).
  - `scaffolded`: template searches (`kind: template_search`; `attempt_count`, `latest_run_id`, `latest_at`, `outcome`, typed `reason_code`) joined to the caller's demands, plus gate attempts (admissions not completed; `reason_code` from `last_error`). `reason_code` is one of `mandate_universe_missing`, `survivorship_limited`, `below_threshold`, `dataset_unavailable`, else the leading token of the owner text, else `reason_not_recorded`.
  - `replicated`: only admissions whose experiment run the owner recorded as `completed` with `metadata.replication_gate.passed == true` (or an entry carrying `replication_gate.passed == true`). Completed admissions without it go to `data.document_checked` (not in `items`, filter with `lane=document_checked`), each with `document_check_reason` from owner facts: `retired` (registry strategy state), `test_scope` (`data_scope == test`), `simulation_or_raw_market_seed` (`mode`/`datasets`/`lineage` say simulation or raw market), else `no_passing_gate_result`; when the runs read fails the reason is `gate_result_unavailable` and nothing is promoted. Names are never the classifier.
  - `data.counts`: `collected_text_records`, `dropped_irrelevant`, `hypothesis_cards`, `templates_registered`, `templates_evaluated`, `searches`, `passes`, each `{value, status}` or `{value: null, status: "unavailable", reason}`; never 0 for a failed read. `hypothesis_cards`, `collected_text_records` and `dropped_irrelevant` are unavailable (`no_bounded_owner_read`) until bounded owner reads exist; the discovered lane then holds demands only.
  - New surfaces: `alpha_factory_demands`, `alpha_factory_runs`, `alpha_factory_hypothesis_cards`, `alpha_factory_source_records`, `alpha_factory_persona_names`.

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

> **GET /bff/alerts read budget and partial composition (BFF-ALERTS-CURRENT-20261107, BFF-ALERTS-PARTIAL-20261109):** runs under its own named budget `PANTHEON_BFF_ALERTS_READ_TIMEOUT_SECONDS`, default **3.0 s**, instead of the generic 0.6 s management default (same pattern as `PANTHEON_BFF_COCKPIT_READ_TIMEOUT_SECONDS`). Reason: the aggregation makes sequential owner HTTP calls (incidents, kill switch, runtimes/telemetry, governance queues); on 2026-11-06 hosted reads, 9 of 10 exceeded 0.6 s and returned empty, and on 2026-11-09 hosted reads (BFF `bdd2b8e54`, VM load ~10), 1 of 30 exceeded 3.0 s (3237 ms) with measured contributor timings `_build_incident_alerts` 24/33/27 ms, `_build_kill_switch_alerts` 0/0/0 ms, `_build_runtime_alerts` 222/318/268 ms, and `_build_governance_alerts` 2109/2154/726 ms (`list_governance_review_queue_items` 661/594/1453 ms, `list_approval_queue_items` 368/282/323 ms). Contributors run in order `incident` -> `kill_switch` -> `runtime` -> `governance` and record each completed contributor into `completed_sections` as it finishes. When the route budget expires after one or more contributors finished, the route keeps HTTP 200 and returns the completed contributors' alerts with `meta.surfaces.alerts.source = bff_composed`, `status = degraded`, and `reason = read_timeout`, and marks each unfinished contributor surface (`incident_feed`, `kill_switch`, `runtime_roster`/`telemetry_summary`, `review_queue`/`approval_queue`) with `reason = read_timeout`. An empty alert list with `meta.surfaces.alerts.source = timeout_fallback` (and `reason` = `read_timeout`, `read_saturated` or `read_error` by actual failure class, plus one warning log) is returned only when no contributor finished. Retired runtimes (status `retired` or `retired_at` set) raise no runtime alert and trigger no telemetry read; incidents are read with `open_only` (open and investigating).
| `/bff/knowledge` | Composed Knowledge inbox over `/api/v1/knowledge/notes`, `/api/v1/knowledge/insights`, and `/api/v1/knowledge/memory` only (EVIDENCE-OWNER-LIST-BOUNDED-20261104: market evidence stays in the evidence explorer, strategy specs are not inbox material, and the route never calls the source-ingest or registry owners; an empty inbox is valid) | `{ data, items, page_info, meta }`; when no backing knowledge store is readable, returns an empty `data/items` list with `meta.surfaces.knowledge_inbox.status: unavailable` and `source: missing` rather than a bare array; a reachable research owner that returns zero notes is an empty inbox (`status` not `unavailable`, notes surface `source: service_client`), and a failed owner read (transport error, non-success status, 404, or non-list body) is that sub-surface `status: unavailable`, `source: unavailable`, never an empty list | `operator` |
| `/bff/strategies`, `/bff/strategies/{strategy_id}` | Strategy registry specs (`research_knowledge_source`) + Telemetry runtime summary of the admitted RuntimeBinding (`management_read_models.service.read_strategy_runtime_truth`, same binding/staleness join as Trading Pulse; the metric owner for strategies with an admitted active binding) + latest ranking snapshot (`ports/rankings.py`, fallback only for strategies without an admitted binding; reducer and fail-closed rules unchanged) | Strategy DTOs expose owner-backed `pnl` (absolute currency value, never converted to percentage), `drawdown` (dimensionless peak-to-trough ratio in `[0.0, 1.0]`), `totalTrades`, `metricsAsOf` (`latest_telemetry_at`), `metricsResolution` (`"fresh"`, `"missing"`, `"ambiguous"`, or `"unavailable"`), and `metricsPersonaId` matched once per request through `strategy_ids` gated by `telemetry_resolution != "missing"` (never by `stage`). For an admitted binding `metricsResolution` is `"fresh"` only with a non-null `pnl`; otherwise `"missing"`/`"stale"`/`"unavailable"` and additive `metricsReason` carries the recorded reason (e.g. `no_fills`, `marks_unavailable`). Additive `runtimeStatus` (`running`, `paused_market_input`, `paused_operator`, `none`, `unknown`) with `runtimeStatusReason` is read once per request from RuntimeBinding (a failed read is `unknown`, never idle); `operationalStatus` is unchanged. Owner calls per list request are constant (RuntimeBinding, Telemetry summaries, Persona directory: one each), and the payload carries no `quarter_equity_observations`. `pnl30d` and `sharpe` are always `null` (`None`) because no owner computes a 30-day window or Sharpe ratio on this path; `POST /bff/strategies` and `PATCH /bff/strategies/{strategy_id}` cannot default or set `pnl30d`, `sharpe`, or `drawdown`. `capitalPoolId` falls back to registry `metadata.capital_pool_id` when `execution_profile`, `governance`, and `summary` lookups are empty, and is `""` only when the owner has none | `viewer` (read) / `operator` (write) |
| `/bff/artifacts`, `/bff/artifacts/{artifact_id}` | Registry strategy-artifacts catalog (`GET /api/registry/strategy-artifacts`, `GET /api/registry/strategy-artifacts/{artifact_id}`) + Research run outputs (`research/client.py` `list_research_artifacts`, `get_research_artifact`) | `/bff/artifacts` merges tenant-scoped Registry `execution_bundle` rows (`source_kind: "registry"`, `kind: "execution_bundle"`) with Research run outputs (`source_kind: "research_run_output"`) sorted newest first (`updatedAt` descending); every row carries owner-backed `id`, `name`, `kind`, `version`, `state`, `risk`, `hash`, `owner`, `updatedAt`, `source_kind`, and `list_scope` (with `version`, `state`, and `risk` kept `null` on Research run outputs). For Registry rows, `list_scope` is `"current"`, `"test"`, `"retired"`, or `"unbound"`, joined once per page from the registry `strategy_id` to its bound persona IDs (`GET /api/registry/strategy-specs?view=persona_binding`, while `get_strategy_labels` keeps its `{strategy_id: {"title", "name"}}` shape) and the persona `listScope` classification via the existing persona directory lookup; Research run outputs carry `list_scope: "research"`. When the persona directory cannot be read, every row carries `list_scope: "unknown"` with `meta.surfaces.persona_directory.status: "unavailable"` (`meta.persona_directory_status: "unavailable"`) and no row is dropped. `strategy_spec` rows are excluded. When one owner read fails, the surviving source is returned with `meta.surfaces.artifact_list.status: "degraded"` naming the failed source (`failed_source` / `failed_sources`) and `page_info.total: null` (`total_complete: false`); when both fail, `meta.surfaces.artifact_list.status: "unavailable"`. `/bff/artifacts/{artifact_id}` resolves either a Registry `registry_id` (respecting tenant scope) or a Research `artifact_id` and returns 404 for unknown or cross-tenant IDs. `/api/v1/research/artifacts` remains Research-only | `viewer` |

#### Persona strategy pool detail (BFF-PERSONA-POOL-VIEWS-20261112)

`GET /bff/personas/{persona_id}` and
`GET /api/v1/operator/persona-management/{persona_id}` add `data.strategyPool`.
Both resolve the authenticated tenant and refuse a foreign persona with 404
before reading its pool, cycles or demands. Existing read-role checks apply.
Both handlers call `PersonaService.read_strategy_pool`; the existing application
service binds `PersonaPoolViewsPort` to its cycle read owner and forwards the
caller authorization, tenant and snapshot time. Routes do not access stores.

| Field | Owner and semantics | `meta.surfaces` key |
|---|---|---|
| `current` | Persona `GET /api/personas/{id}/strategy-pool`; complete immutable version, digest and members (`artifact_ref`, `checksum`, `stage`, `risk_weight`, `baseline_ref`, lineage and removal fields) | `strategy_pool` |
| `versions` | Persona `GET /api/personas/{id}/strategy-pool/versions` → `versions`; every version remains tenant/persona scoped | `strategy_pool_versions` |
| `memberVirtualBooks` | Latest paper daily cycle's `persona_cycle.observe_bundle.books`, joined by exact artifact and checksum; each row includes member stage, weight, baseline reference, `status`, `reason`, and the unchanged owner `book`, including `net_of_costs` and baseline comparison when present | `member_virtual_books` |
| `health` | Latest cycle's owner-computed `observe_bundle.pool_health`: `gross_utilisation`, `drawdown_utilisation`, `effective_bets`, `idle_day_share`, observation window and demand reasons; owner `unknown` values and reasons remain unknown | `pool_health` |
| `cycles` | Existing OODA owner port; latest 20 paper persona-cycle records, newest first, with packet ID, timestamps, status, phase, failure reason, decisions and `pool_autonomy`; private prompts, case files and staged memory are excluded | `autonomy_cycles` |
| `openDemands` | Research `GET /api/research/persona-demands?persona_id={id}&status=open`; original demand records | `open_demands` |
| `memberRules` | One row per distinct `artifact_ref` named in `current` or `versions`, read from Registry `GET /api/registry/strategy-artifacts/{artifact_ref}` (caller authorization, authenticated tenant, per-read timeout bounded by the remaining management read budget; at most one read per distinct ref per request). An `ok` row has `artifact_ref`, `checksum`, `strategy_id`, `version`, `strategy_logic`, `parameters`, `adjustable_controls` (`mutation_surface.controls`), `immutable_parameters` and `expression_policy`, copied unchanged from `metadata.strategy_artifact`. A row whose entry checksum differs from the member checksum, or whose entry is missing or not owned by the tenant, is `unavailable` with a `reason` and no rule body. A Registry failure sets `memberRules: null` and `meta.surfaces.member_rules` unavailable (`source: registry_owner`); every other section is unchanged | `member_rules` |

**Observed pool and membership comparison.** `health` and `memberVirtualBooks` are valid
when the latest cycle's observed pool has the same non-removed members as the current pool,
compared by `artifact_ref`, `checksum`, `stage` and `risk_weight`. `pool_version` and `digest`
may differ: a refused reweight appends a new version with unchanged members and does not
invalidate the observation. When members differ the reason stays `cycle_pool_snapshot_mismatch`.
`pool_health.pool_version` is checked against the observed pool version. Both surfaces carry
`observed_pool_version` whenever a cycle observation exists (also when unavailable).

Pool and Research HTTP reads forward caller authorization and the authenticated
tenant, with a maximum two-second timeout per read (or remaining request budget).
They use `PANTHEON_PERSONA_API_URL` / `PANTHEON_RESEARCH_API_URL`, falling back to
the respective `*_SERVICE_URL`. Cycles, Persona cycle summaries, and OODA packet
list/detail reads share the application's `OodaPacketsPort` (`read_store.ooda_management.ooda`).
When no explicit store or records provider is injected, `OodaPacketsPort` binds the
existing Persona owner's shared record path (`PANTHEON_BFF_OODA_PACKET_STORE`, otherwise
`PANTHEON_OODA_DATA_DIR` / `PANTHEON_CONTROL_PLANE_DATA_DIR` + `ooda_packets.jsonl`)
through the Persona owner's read-only decoder (`read_packets`). Missing or unreadable
files report `unavailable`; no default repository path or new record store is created.
BFF performs no portfolio calculations, owner writes, seed creation or
paper/live transitions.

Book metrics and health are **observations from the latest daily cycle**, not a
new live telemetry evaluation. Their surfaces carry `packet_id` and `observed_at`;
the observation must name the same tenant, persona, pool version and digest as
`current`. A mismatched or missing latest observation is unavailable; an older
observation is never silently substituted. Book metrics require matching member
identity/checksum, provenance, complete marks and owner net-cost metrics.

Each section has `status`, `source`, `reason`, and `snapshot_at` in its surface.
An unconfigured, failed, malformed or misscoped owner yields a `null` section and
`status: unavailable`, with a sanitized reason; independent sections still return.
An individual missing/incomplete/mismatched book remains an identified member
row with `book: null` and `status: unavailable`; the book surface is unavailable.
Successful empty cycle/demand lists are `[]`, distinguished from an unavailable
owner. Missing members, baselines, health inputs or returns are never fabricated.

### 10.1.2 BFF Management Endpoints (BFFGAP-CONSOLE Series)

Endpoints in the `/bff/management/` namespace are BFF-only composite read views served
directly from the source-ingest service registry or aggregated read surfaces.
They follow the canonical list envelope (§5.1) and emit explicit degraded envelopes
when the backing store is unconfigured or unreachable (§7.2).

| Route | Composes | Response Envelope | Degraded Behavior | Min Role |
|---|---|---|---|---|
| `GET /bff/management/data-sources` | source-ingest `/api/source-ingest/registry` | `DataSourcesEnvelope`; `data: { id, items, summary, status, source }`; typed `page_info`; typed `meta.surfaces.data_sources`; no top-level `items` | When source-ingest URL is unconfigured (`source:missing`) or unreachable (`source:unavailable`): empty `data.items`, `meta.status:unavailable`, `data.status:unavailable`, and surface `staleness` — never a bare `[]` | `operator` |
| `GET /bff/management/permissions` | governance permission read surface (`governance_permissions`) | `ManagementRecordsEnvelope`; `data: { id, items, summary }`; typed `page_info`; typed `meta.surfaces.governance_permissions`; no top-level `items` | When the dataset is missing: empty `data.items`, `meta.status:unavailable`, surface `source:missing`, and `meta.degradation.reason` | `operator` |
| `GET /bff/management/memory-governance` | memory governance read surface (`memory_governance_rules`) | `ManagementRecordsEnvelope`; `data: { id, items, summary }`; typed `page_info`; typed `meta.surfaces.memory_governance_rules`; no top-level `items` | When the dataset is missing: empty `data.items`, `meta.status:unavailable`, surface `source:missing`, and `meta.degradation.reason` | `operator` |
| `GET /bff/management/consult-rules` | consult rule read surface (`consult_rules`) | `ManagementRecordsEnvelope`; `data: { id, items, summary }`; typed `page_info`; typed `meta.surfaces.consult_rules`; no top-level `items` | When the dataset is missing: empty `data.items`, `meta.status:unavailable`, surface `source:missing`, and `meta.degradation.reason` | `operator` |
| `GET /bff/lineage` | registry lineage owner (`GET /api/registry/lineage`, tenant-scoped, bounded; `dataset_source("lineage_edges")` reports the real read outcome `service_client`/`unavailable`; registry entry nodes carry `strategy_id` and `version`; `root_id` accepts a strategy id, persona id, artifact/registry id or capital pool id) via `ReadSurfaceStore` plus one bounded `DisplayNameResolver` read per request (`list_personas` / `get_strategy_labels`); `/bff/strategies/{id}/lineage` and `/api/v1/lineage*` and the incident detail read the same port | `LineageEnvelope`; `data: { id, nodes, edges, status, source }` where each node keeps `id`, `label` (`id`), `type`, `state` (plus `strategy_id` and `version` on registry nodes) and adds `display_name` and `name_missing` (resolved for `Persona`, `Strategy` and `Artifact` nodes, `"{strategy_name} ({type})"` for a `CapitalPool` or `Experiment` run node with one owning strategy, and `display_name:null`, `name_missing:true` when the resolver cannot name the node); typed `page_info`; typed `meta.surfaces.lineage` and `meta.surfaces.display_names`; query params: `root_id`, `root_type`, `depth` (default 3, max 20), `artifact_id` | When the registry lineage owner is unconfigured or unreachable (`source:unavailable`/`missing`): empty `nodes`/`edges`/`items`, `meta.status:unavailable`, `data.status:unavailable` — never a bare `[]` (distinct from a reachable owner with an empty graph, which answers `status:ok` with `nodes:[]`). When a display-name resolver read fails, the graph stays readable with `display_name:null`, `name_missing:true` and `status:degraded` on `meta.surfaces.display_names`, `meta.surfaces.lineage`, `meta.status` and `data.status` | `operator` |
| `GET /bff/workflows` | workflow template registry (`workflow_templates`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.workflow_templates` | When the registry is missing: empty `data/items`, `meta.degradation.reason`, surface `status:unavailable` | `operator` |
| `GET /bff/hooks` | hook registry (`hook_registry`) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.hook_registry` | When the registry is missing: empty `data/items`, `meta.degradation.reason`, surface `status:unavailable` | `operator` |
| `GET /bff/knowledge` | composed knowledge inbox over research notes, insights, and memory (no evidence, no strategy specs) | `ManagementRecordsEnvelope`; `data/items` list; typed `page_info`; typed `meta.surfaces.knowledge_inbox` plus source surfaces and `composition_sources` | When no source dataset was readable (all missing or failed): empty `data/items`, `knowledge_inbox.status:unavailable`. A missing (unconfigured) dataset is reported on its own sub-surface and does not force the inbox unavailable or degraded when another dataset was read; a failed read degrades the inbox | `operator` |

**Degraded envelope example** (source-ingest unconfigured in dev):

```json
{
  "data": {
    "id": "management-data-sources",
    "items": [],
    "summary": { "total_items": 0, "returned_items": 0, "status": "unavailable", "source": "missing" },
    "status": "unavailable",
    "source": "missing"
  },
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50, "returned": 0, "has_more": false },
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

MGMT-OPS-001 adds the shared per-persona operations confidence read model:

```
GET /bff/management/operations-read-model/{persona_id}?period=latest
```

The `200` response must publish `OperationsReadModelEnvelope` in OpenAPI. The
envelope contains `identity`, `data_confidence`, finite-or-null `performance`
metrics, `sources[]`, and `diagnostics[]` so management console pages share the
same `formal`/`partial`/`fallback`/`degraded`/`unavailable` source-confidence
semantics instead of synthesizing local fallback rules.

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
| `approval` | `GET /bff/approvals` |
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

## 14A. Research Orchestrator Tasks Read Endpoints (`RESEARCH-TASKS-READ-20261104`)

Research orchestrator tasks (`research.research_tasks`) and their execution runs (`research.research_runs`) are distinct from `research.research_experiments` (which has no background executor). The BFF exposes two read-only HTTP owner-backed endpoints for research orchestrator tasks without merging them into `/bff/research-experiments` or `/bff/experiments`:

| Route | RBAC | Owner Upstream | Surface Key |
|---|---|---|---|
| `GET /bff/research-tasks` | `viewer` (`require_read_role`) | `GET /api/research-orchestrator/tasks` + `GET /api/research-orchestrator/runs` | `research_tasks` |
| `GET /bff/research-tasks/{task_id}` | `viewer` (`require_read_role`) | `GET /api/research-orchestrator/tasks/{task_id}` + `GET /api/research-orchestrator/runs?task_id={task_id}` | `research_tasks` |

- **Verified caller auth and tenant isolation**: Uses the same caller auth and `bound_tenant` helper as `ports/job_read.py` (`verified_caller_tenant`). A task's tenant is read from top-level `tenant_id` or `constraints.tenant_id`. Tasks with neither tenant field are returned to no caller and counted in `meta.excluded_unscoped_count`. Missing tasks, unscoped tasks, and tasks belonging to another tenant return the same non-disclosing `404 RESOURCE_NOT_FOUND` on `GET /bff/research-tasks/{task_id}`.
- **Item projection**: Each item carries `id`, `task_id`, `name` (falling back from `title` to `objective` to `task_id`), `objective`, `status` (exact owner status, never cast to the experiment state machine), `created_by`, `created_at`, `updated_at`, `run_count`, and `latest_run` (`run_id`, `adapter`, `dispatch_mode`, `status`). Stub runs report their real `adapter` and `dispatch_mode` and are never labelled as real executions.
- **Collector query plan proposals (`COLLECTOR-DEMAND-QUERY-PLAN-R2-20261112`)**: Tasks with owner `task_type = "collector_query_plan"` additionally carry a read-only `query_plan` object (revision, matched fixed-vocabulary terms, and the exact `fetch-policy.json` diff it would apply). It is derived deterministically from open persona demands only (no model output, no fetched text), never touches social sources, and is applied only via a reviewed PR to `fetch-policy.json`. Other tasks omit `query_plan`.
- **List views**: `GET /bff/research-tasks` accepts `view=research` for regular research tasks and `view=collector_checks` for collector query plans. The default `view=all` includes both. The selected view applies before pagination, so the total, cursor and hidden-record count refer to that view.
- **Non-real filtering (CONSULT-MEETING-RESEARCH-TRUTH-20261120)**: every task row and detail carries `is_non_real` (bool) and `non_real_reason` (`stub_run` | `manual_record` | `acceptance_test` | null). A task is non-real when its latest run has `adapter` or `dispatch_mode` of `stub`/`manual`, when `data_scope` (task, constraints, latest run or run parameters) is `test`/`acceptance`/`synthetic`, or when the title starts with `ACCEPTANCE TEST`. `GET /bff/research-tasks` hides non-real rows by default; pass `include_non_real=true` to list them. `meta.hidden_non_real_count` (also on `meta.surfaces.research_tasks`) reports the hidden count and `page_info.total` counts the filtered result.
- **Name fallback and display summary**: `name_is_fallback_id` is true when `title` and `objective` are both blank (so `name` is the `task_id`). `display_summary` = `{task_kind, subject_ref, symbol, dataset_ref}`. References come from recorded strings or reference objects, and `dataset_ref` skips structured inputs unrelated to datasets. Missing values are `null`.
- **Open persona demands (BFF-RESEARCH-DEMAND-READ-20261125)**: `GET /bff/research-tasks?view=research` additionally returns `open_demands`, read from the Research owner `GET /api/research/persona-demands?status=open` with the caller bearer and tenant forwarded (no new BFF route). Each entry: `demand_id`, `persona_id`, `persona_display_name` (persona owner record, else null), `short_measure`, `tried_families`, `missing_datasets`, `mandate`, `created_at`, `expires_at`, `status`, `evidence_ref`, `last_template_search_reason` (`discovery.template_search.reason`) and `last_discovery_reason` (`discovery.last_run.reason`). Demands are never invented: an owner failure, denial (401/403), malformed body, or a row of another tenant yields `open_demands: []` with `meta.surfaces.research_demands = {status: "unavailable", reason}` while tasks still render; success is `status: "ok"`. Other views do not read demands. The persona pool view's `openDemands` resolves the same owner from `PANTHEON_RESEARCH_ORCHESTRATOR_API_URL` first, then the older `PANTHEON_RESEARCH_API_URL`/`PANTHEON_RESEARCH_SERVICE_URL`/`RESEARCH_ORCHESTRATOR_URL`/`RESEARCH_ORCHESTRATOR_API_URL`.
- **Demand join and persona scope (BFF-RESEARCH-DEMAND-READ-20261125)**: a template-search task row and detail carry `demand` (`demand_id`, `persona_id`, `attempt_count` = recorded template-search runs of that demand, `last_reason_code` = latest run `rejection.detail` else `template_search.reason`, and `short_measure`/`status` when `view=research` read the demand; otherwise null), joined by run `parameters.demand_id`; other rows carry `demand: null`. The title stays the recorded title; `display_summary.task_kind`/`subject_ref` fall back to `template_search`/the demand id only when empty. Every row carries `persona_ids` (run/task `dataset_lineage` personas plus the demand persona). A `rapid_eval` row is non-real (`acceptance_test`) when its lineage names only personas whose owner record has `data_scope` test or that the existing release-probe predicate (`strategy_stub_reason`, `strategies/routes/common.py`) classifies as a probe; one persona directory read per request, distinct ids only. An unreadable persona owner or unknown persona leaves the row visible with `non_real_reason: null`.
- **Degradation contract**: Unconfigured `PANTHEON_RESEARCH_ORCHESTRATOR_API_URL` or owner HTTP failure on `GET /bff/research-tasks` returns `meta.surfaces.research_tasks.status = "unavailable"` (never an empty `"ok"` success); a healthy owner with zero tasks returns `status = "ok"`.

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

## Evidence explorer owner read (EVIDENCE-OWNER-LIST-BOUNDED-20261104 / BFF-EVIDENCE-SUMMARY-POPULATION-20261105)

- `GET /api/source-ingest/evidence/items` (owner) accepts `page_size` (1-500, default 200) and `page_token` and returns `{items, total, page_size, next_page_token, summary}`. List rows omit `metadata.raw_storage_refs` and `metadata.calendar_evidence`; `GET /api/source-ingest/evidence/items/{id}` still returns the full record. The `summary` object (`{total, verified_count, verified_evidence, by_source_type, by_link_type, by_credibility_tier}`) aggregates across the full tenant population (`summarize_evidence_items(tenant_id=tenant)`), excluding rows without a tenant (`tenant_id` absent or empty) and foreign tenant rows identically to `list_evidence_items_page`.
- `GET /bff/management/evidence/{ref_id}` (BFF-EVIDENCE-DETAIL-AND-PAGING-20261125) answers ONE flat detail object in `data` (never a list envelope): `{id, ref_id, title, display_label, source_class, source_type, source_ref, captured_at, link_type, source_document{title, source_type, source_ref, captured_at, excerpt}, credibility{tier, verified, confidence[, verification_method, last_verified_at]}, linked_object_summary, linked_decisions[], resolved_link{availability, route_href, display_label, open_in_new_tab}, summary, route_href, management_href, redacted}` with `meta.surfaces.evidence_ref_detail`. It reads the single item through the owner item route (`GET /api/source-ingest/evidence/items/{id}`) and uses the same redaction and the same public-item mapping as the list and `personas/service.py` (`_management_evidence_public_item`); a redacted item carries only `id`, `ref_id`, `display_label` (= ref id), `kind`, `required_capability`, `reason`, `redacted: true` (no title, excerpt or link). Status: `404` only when the owner itself answered 404 (or the item is outside the caller's tenant); an owner timeout, 401/403, 5xx, transport failure or capacity saturation answers HTTP 200 with `meta.surfaces.evidence_ref_detail.status: "unavailable"` plus a `reason` (`read_timeout`, `read_capacity_saturated`, `read_error`) and null fields. A tenant item stored under a bare record id that the list shows is also readable by detail. `GET /bff/management/evidence?ref_id=` keeps the list envelope.
- Credibility and links (same RC6 task): `source_class` is the recorded `metadata.source_class`. `credibility.verified` is `true` only when `metadata.verification` carries both a `method` and a time (`verified_at`/`time`) and is otherwise `null` (unknown); the connector `confidence` is shown as a number (`null` when absent) and never decides verification, so SQL (`evidence_verified`, `evidence_credibility_tier`) and BFF agree on a null confidence. `summary.verified_count` / `verified_evidence` are `null` (the confidence-derived count was removed). `resolved_link` is `external` with the recorded public URL for `http`/`https` source references and `unavailable` for every other scheme (`gs://`, `file://`, `javascript:`, provider URIs such as `tw-official://`).
- Paging cost: the owner selects page item ids first from `source_evidence_page_idx (evidence_tenant_id, append_id DESC) WHERE record_type='evidence_item'` and reads payloads only for that page, so token 0, 20000 and 41000 of a 43000-row table answer in the same low-millisecond bound. `COUNT(*)` for the tenant total is an index-only scan of the same partial index (bounded by the tenant row count, not by the offset); the population `summary` is computed only when `include_summary` is not false and is one GROUP BY over `source_evidence_summary_idx`, not a per-page payload read.
- A failed page read is HTTP 200 with `meta.surfaces.*.status: "unavailable"`, `page_info.total: null`, `data.items: []` and null summary fields; it is never an empty list with `total: 0`. A read that succeeded and found nothing is `status: "ok"` with `total: 0`.
- `/bff/management/evidence` serves an unfiltered page with exactly one bounded owner page read; `data.summary` (`verified_count`, `verified_evidence`, `by_source_type`, `by_link_type`, `by_credibility_tier`) describes the whole tenant population from that single owner response and is identical across pages (e.g., page 1 and page 2). Filtered reads stream every owner page so `page_info.total` and population summary counts stay correct across the matching population.
- When the owner summary/count read fails while the bounded page read succeeds, `page_info.total`, `data.summary.total_evidence`, and `data.summary.total_items` remain the bounded list total while the affected summary fields (`verified_evidence`, `verified_count`, `by_source_type`, `by_link_type`, `by_credibility_tier`) are `null` (never a page-sized number). When the entire owner read fails or times out, `management_evidence`, `evidence_refs`, and `knowledge_evidence` are all `status: unavailable` and all `data.summary` totals and bucket fields (`total_evidence`, `total_items`, `returned_evidence`, `visible_evidence`, `redacted_evidence`, `verified_evidence`, `verified_count`, `by_source_type`, `by_link_type`, `by_credibility_tier`) are `null`, not `0`. Availability is decided by the read that produced the rows, never by shared instance state.
- BFF-EVIDENCE-OWNER-BUDGET-20261106: the evidence-local owner read bound is 4.0 s (`EVIDENCE_OWNER_READ_TIMEOUT_SECONDS`, was 2.0 s) and the evidence route budget is that bound plus the existing 0.5 s margin (4.5 s). `PANTHEON_BFF_EVIDENCE_OWNER_READ_TIMEOUT_SECONDS` and `PANTHEON_BFF_EVIDENCE_READ_TIMEOUT_SECONDS` still override. The global management read default (0.6 s) and the human inbox budget are unchanged. A slow-but-correct owner answers `ok`; a hung owner is still `unavailable` with null summary fields.
- Source-ingest items map through one normalizer: `display_label` and `source_document.title` from `citation_label`, `summary` from `body`, `source_document.captured_at` from `available_time` then `event_time` (never the current time), `source_document.source_ref` from `content_ref`, `source_document.source_type` from `metadata.source_class`.
- `GET /bff/management/human-inbox` reads incidents once through the owner's `GET /api/incidents?open_only=true` (status open or investigating) and never makes an unfiltered incidents request. Items come from one canonical projection shared with the NL-ask inbox payload (`_human_inbox_project_items`). A resolved or closed incident is not an item and not counted. A proposed approval decision is `action_state: pending` and is listed once (its `review-<decision_id>` governance review is dropped). A governance review of `item_type: EvolutionDecision` whose `allowedActions` grant neither approve nor reject is not listed until it becomes decidable. A row never takes the request time as its `created_at`; a source without a time reports `null`.
- Contributors (approval_queue, governance_review_queue, incidents, persona_readiness) are read concurrently, each bounded by `human_inbox_surface_timeout_seconds`. A contributor whose owner read succeeded with zero rows is `status: ok`; a failed read is `degraded` with `reason: contributor_read_error` and a timeout is `degraded` with `reason: read_timeout`, with `meta.partial: true` and the other contributors' items returned. The route budget is the contributor bound plus 1.5 s; when it expires, contributors that completed are still returned. A fallback payload carries `read_timeout`, `read_capacity_saturated` or `read_error` according to the exception that occurred.

## Cited evidence list (BFF-EVIDENCE-CITED-LIST-20261125)

Operator decision 2026-11-25 (RC6): the evidence library lists only references that decisions, meetings, approvals and research actually cite; raw ingest stays in its data source.

- `GET /bff/management/evidence?scope=cited[&page_size=1-100&page_token=]` answers the list envelope with `data.id: "management-evidence-cited"`, `data.scope: "cited"`, `data.items[]`, `data.summary`, `data.sources`, `page_info{next_page_token, total, page_size}` and `meta{surfaces.cited_evidence, surfaces.cited_evidence_sources, item_reads, cost, redacted_evidence_count}`. Without `scope` (or `scope=ingested`) the answer is the unchanged ingested list. `ref_id=` resolves an ingested item through the existing path in every scope. Any other `scope` value is `400 VALIDATION_FAILED`.
- One row per cited reference: `{id, ref_id, ref_kind, source_owner, label, name_missing, label_parts, source_class, resolved_link{route_href, evidence_ref_id}, route_href, cited_as[], cited_by[], citation_count, last_cited_at, redacted}`. `cited_by[]` entries are `{entity_type, entity_ref, label, parts, route_href, time, source}` with `entity_type` one of `meeting_proposal`, `meeting_position`, `approval`, `evolution_proposal`, `research_demand`, `research_ticket`, `persona_cycle_decision`. Rows are ordered by the latest citation time, newest first.
- Citations come only from recorded fields through existing owner reads, with the caller's identity and tenant: meeting proposal `citations` (`GET /api/consult/meetings/proposals`, one call); meeting `rounds[].positions[].citations` (`GET /api/consult/meetings?limit=30`, then one `GET /api/consult/meetings/{id}` per meeting for the 30 newest, at most 4 in flight); governance approval `evidence_refs` (`GET /api/governance/approvals`, one call); evolution proposal `evidence_refs` (`GET /api/evolution/proposals`, one call); research demand `retrieval_influence.metadata.cited_item_ids` (`GET /api/research/persona-demands`, one call); research ticket submission `evidence_refs` and `human_input.evidence_refs` (`GET /api/research/tickets?meeting_linked=true&page_size=100`, one call); persona daily cycle `persona_cycle.decisions[].cited_case_file_item_ids` (shared OODA owner port, one read). A case-file item that was only supplied is never listed.
- Reference shapes: `facts:registry_entry:<artifact>` and a bare `artifact-…` id are the same `registry_artifact` row (`ref_id` = artifact id, both forms in `cited_as`); `facts:strategy_pool:<persona>@<version>` (`strategy_pool`), `facts:member_virtual_book:<artifact>` (`member_virtual_book`), `lessons:memory_<layer>:<mem>` (`memory`), `evidence:search_excerpt:ko-evi-…` (`knowledge_object`), owner `{ref_type, ref_id}` and `{kind, artifact_id}` refs keep their recorded kind. `facts:meeting_topic:<meeting>` is the meeting itself and is not listed (counted in `summary.excluded.meeting_topic`). A `ko-evi-<x>-<h>` knowledge object resolves to evidence item `evi-<x>` only when `sha256("evi-<x>")[:12] == h`, through the existing owner item read, and only for the visible page.
- Labels are names only; the BFF composes no sentences. A row `label` is the display name from the existing resolver (registry artifact and virtual book: the label of the strategy that the Registry owner records for that artifact; strategy pool: persona name; resolved evidence item: its title) and `label_parts` carries structure (`artifact_id` + `strategy_id`, or `persona_id` + `pool_version`); `ref_kind` tells the frontend what the name is. An id no directory can name keeps `label = ref_id` and `name_missing: true`. A `cited_by[].label` is the citing entity's recorded title only (proposal/approval/evolution/ticket `title`, demand `short_measure`, meeting topic with persona ids replaced via `present_title`) or `null`; `cited_by[].parts` carries the pieces for translation: meeting position `{meeting_id, meeting_name, round, position, persona_id}`, meeting proposal `{meeting_id, kind}`, approval `{decision_type, target_id}` plus `target_display_name` when `target_id` is an artifact whose Registry read on this page answered a named strategy (`label` stays `null`), evolution proposal `{persona_id, kind}`, research demand `{persona_id, short_measure}`, research ticket `{status}`, persona cycle decision `{persona_id, persona_name, kind, decision_index}`.
- BFF-EVIDENCE-CITED-NAMES-LINKS-20261125 artifact names: the label directory (`get_strategy_labels`) is keyed by strategy id, so a registry artifact or virtual book is named through one Registry owner read per distinct artifact id on the visible page (`read_registry_strategy_artifact_detail` -> `GET /api/registry/entries/{artifact}`, the read behind `GET /bff/artifacts/{id}`, with the caller's token and `X-Tenant-Id`). `label_parts.strategy_id` is the recorded `strategy_id` of that entry and is never derived from the text of the artifact id; `label` is that strategy's display name. A failed, slow (past the request read deadline), not found, other-tenant (`tenant_id`/`owner_tenant` not the caller's tenant or `__builtin__`) or over-budget read leaves `label = ref_id`, `name_missing: true`, `label_parts.strategy_id: null`, and is counted in `meta.item_reads`.
- Links point only at management pages that exist (frontend `src/App.tsx`), otherwise `null`: registry artifact and virtual book `/management/artifacts/{artifact}` (never `/management/strategies/{artifact}`, which answers 404 for an artifact id), strategy pool `/management/personas/{persona}`, an evidence item resolved by the item read or an ingested `evi-`/`evref-` id `/management/evidence/{ref_id}`; meeting citations `/management/meetings/{id}`, approvals `/management/approvals`, evolution proposals `/management/evolution-journal`, demands and cycle decisions `/management/personas/{persona}`. Memory, research note, committee memo, unresolved knowledge object and research ticket references have no page and answer `route_href: null` / `resolved_link: null`.
- Per-source status in `data.sources.<key>` and `meta.surfaces.cited_evidence_sources.<key>`: `{status: ok|partial|failed|denied, reason, read_count, truncated, failed_count, citation_count, withheld_count, calls, response_bytes}`. A 401/403 from an owner is `denied` and the other sources are still listed (a tenant-scoped viewer who may not read approvals still gets the rest). One failed meeting detail marks `meeting_positions` `partial`, not failed. Any source that is not `ok`, a truncated source, or more than 500 references sets `summary.incomplete: true`, `summary.complete: false` and `meta.surfaces.cited_evidence.status: "degraded"`. When every source fails the answer is `status: "unavailable"`, `items: []`, `page_info.total: null` and `summary.reference_total: null`; a source failure never becomes an empty ok list.
- Withholding: private persona memory and trader items are never listed, for any role (meetings withholding rule `_collect_meeting_withheld_and_visible_refs` in its strictest form, the case-file projection `project_case_file` with no private persona, and the reference kind, e.g. `memory_persona_private`). Memory fails closed: every memory reference (`lessons:*`, `*:memory_*:*`, bare `mem-…`, or an owner ref whose kind names memory) is withheld and counted (`withheld_count`, `summary.excluded.memory_scope_unproven`) unless the recorded case-file item it cites carries `relevance_scope` (or `memory_scope`/`visibility_scope`) `persona_and_committee` and no private marker; a memory id cited by a proposal, approval or demand has no recorded item and is therefore withheld. Other-tenant owner rows are dropped. Every row passes `redact_evidence_refs` (`default_kind="artifact"`, fail-closed); a redacted row carries `ref_id`, `ref_kind`, `required_capability`, `reason`, `redacted: true`, `label = ref_id`, `resolved_link: null` and gets no item read.
- Bounds and cost: at most 500 references (newest citations kept, `summary.references_truncated`), at most 20 owner reads for the visible page, shared by evidence item reads and Registry artifact reads in page order on one executor (`meta.item_reads{attempted, resolved, failed, truncated}`), at most 30 meeting details with 4 in flight; one call for each other source. `meta.cost` reports `owner_calls`, `response_bytes`, `item_reads` for the request. The route budget is `PANTHEON_BFF_CITED_EVIDENCE_READ_TIMEOUT_SECONDS` (default 8.0 s); a source not finished 0.25 s before the deadline is `failed` with `read_timeout`. No new store, writer, index or cross-request cache.

## Management evolution journal evidence classification and server-side view filter (EVOLUTION-JOURNAL-EVIDENCE-CLASS-20261104)

- BFF-EVO-ALPHA-PAGE-TRUTH-20261107: one journal row per evolution decision. The derived mutation review projection is carried inside the decision row as the nested `mutation_review` object (with `approval_decision`, `allowedActions` and `review_chain` when the owner projection provides them) and is no longer emitted as a separate `entry_type == "mutation_review"` row. `source_type=mutation_review` selects the decision rows that carry a `mutation_review`. The row `status`, `summary`, `target` and `record` are the decision's own.
- Every journal item derived from an `EvolutionDecision` (`entry_type == "evolution_decision"`, as well as the nested `decision` / `mutation_review` / `record` payload) carries `evidence_class`:
  - `"threshold_signal"` when a breached threshold snapshot (`breached=True`) has `signal_type` in `{"performance_degradation", "execution_drift", "feature_drift", "human_correction"}`.
  - `"incident_followup"` when the only metric is `"incident_followup_required"`.
  - `"incident_severity"` when the only metric is `"severity1_incident_count"`.
  - `"unclassified"` otherwise.
- `GET /bff/management/evolution-journal` accepts query parameter `view` with values `all`, `history`, and `auto_proposals` (default `all` when omitted so existing callers are unchanged; invalid values return HTTP 400 `VALIDATION_FAILED`):
  - `view=history`: returns `freeze_order` and `rollback` items, `postmortem` items not in status `draft`, every decision row not in state `proposed`, and `proposed` decision rows with `evidence_class == "threshold_signal"`.
  - `view=auto_proposals`: returns the remaining `proposed` decision rows and the `draft` postmortems; `page_info.total` equals `data.auto_proposal_counts.total`.
  - The `view` filter runs before pagination so `page_info`, `next_page_token`, and `data.summary` describe the filtered set.
  - Deep-link override: when explicit target filter `decision`, `mutation_review`, or `persona` is supplied, the `view` filter is bypassed so a deep link returns its target decision row under any `view`; `decision` and `mutation_review` both match the decision row by `source_id`.
- `data.auto_proposal_counts` is returned on every response with `{threshold, incident_followup, incident_severity, other, infrastructure, postmortem_draft, total}` (BFF-EVO-JOURNAL-POOL-VERSIONS-20261125). Each `proposed` decision is counted once, in exactly one of the five parts (infrastructure decisions first, then by evidence class), so `threshold + incident_followup + incident_severity + other + infrastructure == total`. `postmortem_draft` is the owner total read through `list_postmortems_page` and is a separate count, never part of `total`. `threshold` proposals are history rows, so `view=auto_proposals` lists `total - threshold` decision rows plus the drafts.
- `data.summary.decision_count` counts each decision once. `data.summary.mutation_review_count` counts only decisions that have their own review record (`approval_decision_id`); the projection nested in every decision row is the same decision and is not a second quantity. `pending_review_count` counts those reviews whose `decision_state` is `pending`, `reviewed`, `under_review` or `in_review`.
- Strategy pool versions (BFF-EVO-JOURNAL-POOL-VERSIONS-20261125): for each `paper_owner` persona of the caller's tenant (at most 20 personas, one persona-owner `GET /api/personas/{id}/strategy-pool/versions` read each, plus the one persona list and at most 100 linked-incident reads by id (no full incident list)) the journal returns one `entry_type=pool_version` row per owner version with `persona_id`, `persona_name`, `pool_version`, `action` (`initial` for v1, else the owner decision action), `decision_status` (`applied` or `refused`), `status` (same value), `refused`, `membership_changed`, `reasons` (the recorded owner reasons), and `members_before` / `members_after` (`artifact_ref`, `stage`, `risk_weight`) computed from consecutive owner versions. A refused version has equal before/after members, `membership_changed=false` and is worded "Refused ...", never as an applied change. `summary.pool_version_count` and `summary.refused_pool_version_count` count them. `meta.surfaces.strategy_pools` is `ok`, `degraded` (some personas unread or over the limit) or `unavailable` (reason names the owner failure); the other sources still render. `meta.pool_reads` reports the reads made and their bound.
- Journal item `target` types (BFF-EVO-JOURNAL-VERSION-STRING-20261126): on every `entry_type` (`evolution_decision`, `pool_version`, `freeze_order`, `postmortem`, `rollback`) `target.type`, `target.id` and `target.version` are each a string or `null`, never a number. A `pool_version` row carries the version as text in `target.version` (`"6"` for v6) while its top-level `pool_version` field stays an integer; a numeric owner `target_version` / `to_version` is returned as its text.
- Subject class: every decision row carries `subject_class` = `strategy`, `infrastructure`, `deployment`, `test` or `unclassified` (the linked incident could not be read or was beyond the 100 per request read cap; never shown as a confirmed class, visible by default, counted in `summary.unclassified_decision_count` and `summary.incident_class_reads`), decided only from recorded owner fields (decision `runtime_binding_id` prefix `infra-subject-`, the linked incident's cluster/binding/plan/authority fields, the target persona's recorded `data_scope`), never from a name pattern. `view=history` hides `infrastructure` and `test` entries unless `include_system=true`; `summary.hidden_system_counts` returns the hidden counts. An explicit `decision`, `mutation_review` or `persona` link bypasses the hiding so a link naming one decision still opens it. `summary.decision_subject_class_counts` counts the returned decisions per class.
- Postmortem owner reads use the bounded `list_postmortems_page` from `POSTMORTEM-BFF-OWNER-READ-20261104` so the postmortems owner is never asked for all 2027 draft postmortems on each page read.

## Shell summary running_jobs surface and bounded source-ingest job list (BFF-SHELL-SUMMARY-JOBS-SOURCE-20261105)

- `GET /bff/management/shell-summary` decides `surfaces.running_jobs` from the jobs read itself (`JobReadPort.list_jobs_bff` diagnostics), never from a `dataset_source("jobs")` lookup:
  - all configured sources answered: `status=ok`, `source=bff_composed`;
  - some sources failed, were unconfigured, or exceeded the shell-summary jobs budget: `status=degraded`, `source=bff_composed`, `degraded_sources={<source>: unreachable|unconfigured|timeout}`; `counts.running_jobs` covers the healthy sources;
  - every configured source failed: `status=unavailable`, `source=service_client`, `degraded_sources` names them (never reported as zero with `status=ok`);
  - no job source configured at all: `status=unavailable`, `source=missing`.
- `counts.running_jobs` is the number of jobs in `running`, `in_progress` or `queued` across sources. The read is bounded: one owner page (`page_size=200`, status filter) per source, all sources read concurrently, whole read capped by `PANTHEON_BFF_SHELL_SUMMARY_JOBS_BUDGET_SECONDS` (default 1.5 s, further capped by the active management read budget). A stalled source is reported as `timeout`.
- The process-wide count cache entry is keyed by the caller (authorization + selected tenant) as well as the store, so one caller's failed or healthy jobs read is never served to another caller.
- `GET /api/source-ingest/jobs` keeps its unbounded `{runs, receipts}` form. Supplying any of `status` (comma-separated run statuses), `page_size` (1..250, default 100) or `page_token` selects the bounded form: newest runs first, receipts only for the returned runs, plus `page_info {total, page_size, next_page_token}`. No second list route exists. The BFF job port always uses the bounded form and follows `next_page_token`, so `/bff/jobs` totals span every owner page.

## Management evolution qualification (EVOLUTION-PROGRAM-QUALIFY-R2-20261105)

- `GET /bff/management/evolution-qualification` is a read-only projection of the Evolution owner `GET /api/evolution/program-qualification`. It needs a read role and carries the caller's identity to the owner; the BFF never computes or caches a qualification of its own.
- Query: `page_size` (1-200, default 50), `offset` (default 0), `qualified` (optional boolean filter).
- `data` carries `policy_version`, `evaluated_at`, `evaluated`, `qualified`, `drafted`, `counts_by_first_failing_criterion`, `failure_reason` (non-null when the policy was missing or invalid and nothing was drafted), `total`, `limit`, `offset` and `items`.
- Each `items[]` entry has `decision_id`, `target_id`, `action_type`, `metric_name`, `qualified`, `first_failing_criterion`, `program_id`, `drafted` and `criteria`, a list of all six criteria in order (`real_threshold_signal`, `real_target`, `persistent_breach`, `fresh_evidence`, `executable_action`, `no_open_duplicate`), each with `result` (`passed`, `failed` or `not_evaluated`), `observed`, `threshold` and `reason`.
- A missing owner URL or token, an owner failure or a malformed owner answer returns HTTP 503 `DEPENDENCY_UNAVAILABLE`; it is never rendered as an empty report.
- `data.member_baselines` (BFF-EVO-JOURNAL-POOL-VERSIONS-20261125) lists every live (`trial` or `active`) pool member of each `paper_owner` persona of the caller's tenant: `persona_id`, `persona_name`, `artifact_ref`, `stage`, `risk_weight`, `pool_version`, `status` (`approved`, `no_approved_baseline` or `unavailable`) and `reason`. Status and reason come from `services.evolution.approved_baselines.resolve_member_baseline` applied to the Registry owner entry of that member; the reason is passed through as returned, an unreadable entry is `unavailable` with `registry_entry_unreadable:<error type>`, and no baseline number is read, defaulted or written. `data.member_baselines_surface` carries `status` (`ok`/`degraded`/`unavailable`), `reason`, `approved`/`not_approved`/`unavailable` counts and `reads` (persona list, pool-version reads at most 20, Registry entry reads at most 50). No new route and no writer.

## Strategy list and detail bounded owner reads at real 4898-strategy scale (BFF-STRATEGY-READ-REAL-SCALE-20261106)

- `GET /bff/strategies` delegates pagination and `state` / `persona_id` filtering to the Registry owner via `GET /api/registry/strategy-specs?view=strategy_summary_page&page_size=<n>[&state=...][&persona_id=...][&page_token=...]`. The Registry owner selects only flat summary JSONB leaves in Postgres (`list_strategy_spec_summary_rows`), resolves the current semver revision per strategy family, excludes `retired` families, applies `state` (`draft`, `review`/`candidate`, `approved`, `deployed`/`active`) and `persona_id` filters before slicing, and returns `{items, total, page_size, next_page_token, page_info}` so only one page of summaries crosses the HTTP boundary into the BFF while `page_info.total` and `meta.total` remain exact across the full visible tenant inventory.
- `GET /bff/strategies/{strategy_id}` reads only that strategy family from the Registry owner (`GET /api/registry/strategies/{strategy_id}/strategy-specs`), backed by a targeted SQL predicate `payload ->> 'strategy_id' = %s` in `PostgresRegistryStore.list_by_strategy` and per-request owner-read caching in the BFF so the single-strategy read executes once per request and never scans the full `registry.entries` table.
- `GET /bff/strategies/{strategy_id}` projects a read-only `data.spec` block from the same resolved current owner version without adding a second read or exposing arbitrary metadata: `{spec_version_id, spec_version, market_scope, execution_profile, evaluation_plan, metadata}` where `metadata` is strictly allowlisted to `{market, execution_context, traits: {instruments, time_horizon}}`. Only values actually present in the resolved owner version are emitted; absent or empty sub-objects remain `null` (and `evaluation_plan.benchmark` is never invented when absent from the owner spec). Paged list items (`GET /bff/strategies`) omit `spec`.
- Strategy DTO `alpha` is populated strictly from `market_scope.alpha` and is `""` when `market_scope.alpha` is absent or empty; `source_kind` and `hypothesis_excerpt` are never projected into `alpha`, ensuring `GET /bff/strategies` and `GET /bff/strategies/{strategy_id}` agree on `alpha`.
- Tenant isolation, deterministic `(last_modified_at DESC, strategy_id DESC)` paging, and ranking metric semantics (`fresh`, `missing`, `unavailable`, `ambiguous`, and real `0.0`) remain unchanged.

## Strategy list exclude-draft filter and per-state counts (BFF-STRATEGY-LIST-DEFAULT-VIEW-20261106)

- `GET /bff/strategies` accepts an optional `exclude_state` query parameter (e.g. `exclude_state=draft`). When `exclude_state=draft` is supplied, only non-draft, non-retired strategies are returned with an exact `page_info.total` and `meta.total` across the filtered tenant population and stable `(last_modified_at DESC, strategy_id DESC)` pagination. Sending both `state` and `exclude_state` together returns HTTP 400 `VALIDATION_FAILED`.
- Without `exclude_state`, `GET /bff/strategies` preserves the exact existing `items` and `page_info.total` behavior for all existing callers.
- `meta.state_counts` reports the count for each normalized DTO `state` value (`draft`, `review`, `approved`, `paper_running`, `canary_running`, `live_running`, `deployed`, `paused`, `retired`) for the caller tenant from the same single bounded Registry owner read (`GET /api/registry/strategy-specs?view=strategy_summary_page`). When the `state_counts` read fails or is unavailable, `meta.state_counts` is `null` (never fabricated zeros) while the strategy list still returns HTTP 200.
- `GET /bff/strategies?state=retired` returns the caller tenant's retired strategies (which remain excluded from the default list and from `exclude_state=draft`), and `GET /bff/strategies?state=review` returns `candidate` / `review` strategies.
- A strategy whose stored lifecycle value is not a known state remains visible under `GET /bff/strategies?exclude_state=draft` (with its raw value preserved in `lifecycleStatus`) and is never hidden by the DTO display fallback to `draft`.


## Strategy operational truth (BFF-STRATEGY-OPERATIONAL-TRUTH-20261107)

Read-only. No write route is added, and no Registry or Persona owner service code changes.

- Every `GET /bff/strategies` row and the `GET /bff/strategies/{strategy_id}` DTO carry `personaStates` (a list of `{personaId, state}`) and `operationalStatus`. `state` is the Persona state `GET /bff/personas` reports for the same persona in the same tenant: both pages read `_get_persona_directory_snapshot(tenant)` and project each record with `_persona_record_projected_state`. The directory is read at most once per request. A persona id the directory does not contain has state `missing`.
- `operationalStatus` comes from one helper (`strategies/routes/common.py`): `running` when any bound persona state is in `_PERSONA_OPERATIONAL_LIFECYCLE_STATES`; `retired` when the row has bound personas and every one is `retired`; `unbound` when the row has no persona id; `unknown` when the directory read failed; `idle` otherwise.
- When the directory read fails (or is not wired), every row has `operationalStatus: "unknown"`, each `personaStates[].state` is `unknown`, and `meta.surfaces.persona_states.status` is `unavailable`. No row is dropped or hidden.
- `GET /bff/strategies` accepts `hide_retired_personas=true`, which removes rows whose `operationalStatus` is `retired` before paging, so `page_info.total`, `next_page_token` and the page size describe the filtered set. It walks the owner summary pages of the candidate set with `page_size=200`. Without the parameter the rows, paging and `state_counts` are exactly what the Registry owner page returns.
- `meta.operational_counts` (`running`, `idle`, `retired`, `unbound`, `unknown`) counts the candidate set selected by `state` / `exclude_state` / `persona_id` before the hide filter. `meta.hidden_retired_persona_count` is the number of rows the filter removed (0 without it). Without `hide_retired_personas`, counting needs the whole candidate set, so it is computed only when that set fits one owner page (at most 200 rows, one extra owner read when the requested page is smaller); a larger set keeps the single bounded owner read, reports `operational_counts: null` and `meta.surfaces.operational_counts.status: "unavailable"`. With `hide_retired_personas=true` the counts are always present.
- BFF-STRATEGY-SYNTHETIC-STUBS-20261120: `GET /bff/strategies` (default view and `exclude_state=draft` alike) also hides unbound synthetic and raw-seed stub strategies unless `include_test_personas=true` (the same parameter that returns test-persona strategies). A stub is a row with `data_scope` (row or metadata) in `test`/`acceptance`/`fixture`/`synthetic`, or all personas test-scoped (`test_persona_probe`), or no `persona_ids` and a `strat-tw-official` id / `TW official ` title (`raw_market_seed_without_rules`) or a `SIMULATION ` title (`simulation_fixture`). A strategy bound to a live persona is never a stub. `meta.hidden_unbound_stub_strategy_count` and `meta.hidden_test_persona_strategy_count` report the hidden rows; `page_info.total` excludes them. Classification is read-only over the summary row and the single persona directory read. When the bounded owner page is used, stubs are removed from the returned page and the count is exact whenever the candidate set was read for `meta.operational_counts`, otherwise it counts the returned page.
- Every list row and the detail DTO carry `persona_display_names` (persona id to display name, falling back to the id), taken from the same single persona directory read (no extra owner call per row). The detail DTO also carries `is_synthetic_or_stub` and `stub_reason` (`raw_market_seed_without_rules`, `simulation_fixture`, `test_persona_probe`, or `null`).
- `GET /bff/meetings` and `GET /bff/management/meetings` accept `include_failed` (default false) and forward it to the consultation owner; `execution_state`, `effective_status` and `data.summary.dead_letter_count` stay on the response next to the persona display names.
- Every row carries `symbols`, from `market_scope.symbols` of the Registry summary row (no extra owner read; `[]` when absent).
- The detail `data.spec` block adds `title`, `hypothesis` and `objective` (string or `null`) from the same resolved current StrategySpec version; the projection stays an allowlist.
- The detail DTO carries `data.rules`, read once per request from `GET /api/registry/strategies/{strategy_id}/strategy-artifacts?artifact_state=approved`: one entry per approved `execution_bundle` with `registryId`, `version`, `role`, `strategyLogic`, `parameters`, `adjustableControls` and `immutableParameters` (from `metadata.strategy_artifact`, including its `mutation_surface`). `role` is `pool_envelope` for a bundle whose `strategy_logic.kind` is `persona_portfolio_v1` (it also carries `combinationRule`, `riskLimits` and `members`: `registryId`, `riskWeight`, `strategyLogic`, `parameters`, `adjustableControls`, copied unchanged from the envelope artifact); `rollback_baseline` when the registry id appears in `lineage.parent_registry_ids` of another approved bundle that is not an envelope; `pool_member` when it is named only by pool envelopes (as a member or in their `lineage.parent_registry_ids`); otherwise `trading`. A strategy with no approved bundle returns `rules: []`; a failed read returns `rules: null` and `meta.surfaces.strategy_rules.status: "unavailable"` while the detail stays 200.
- Cost of the rules read: the Registry route lists the strategy family with the same targeted SQL predicate as the `strategy-specs` family read (`payload ->> 'strategy_id' = %s`, `list_by_strategy`), then filters execution bundles in Python; it never scans the artifact catalog.


## Strategy list owner-side hide, exact counts and one bounded read per filter (REGISTRY-STRATEGY-LIST-OWNER-HIDE-20261125)

- Class RC6. `GET /bff/strategies` without `include_test_personas` sends the Registry owner one `GET /api/registry/strategy-specs?view=strategy_summary_page` per request with the owner-side hide input `hide_stubs=true` and `hide_persona_ids=<test persona ids of the single Persona directory read>` (empty when the directory read failed; the stub rule needs no directory). The Registry excludes the rows of the one `strategy_stub_reason` rule (`data_scope` test/acceptance/fixture/synthetic; all personas test-scoped; unbound `strat-tw-official` / `TW official ` / `SIMULATION `; the `Pantheon Dev Paper Release … Attempt …` title only for rows that have no `data_scope`) in the same candidate pass that selects, counts and slices the page. No filter, `state=`, `exclude_state=` and `persona_id=` all make one owner page call and never walk the owner pages. The page is not prefetched ahead of the directory read because its hide input needs the directory's test persona ids; with `include_test_personas=true` nothing is hidden and the page is still prefetched in parallel with the directory read.
- `page_info.total` and `meta.total` are exact on every filter and equal the owner's `visible_state_counts` for the same filter (`state=draft` → `visible_state_counts.draft`; no filter → the non-retired visible counts; `state=retired` → `visible_state_counts.retired`). The approximate total of BFF-STRATEGIES-DEFAULT-PAGE-BOUNDED-READ-20261125 (stubs counted only on the owner pages that happened to be read) is removed: `hide_retired_personas=true` also sends the hide input, so its total and `meta.hidden_*` counts are exact.
- `meta.state_counts` keeps its present meaning (every current version, hidden or not). `meta.visible_state_counts` is added: the rows the owner did not hide, per DTO state. It is `null` — never zeros — when the owner read failed or the owner did not answer the hide input (older Registry), and equals `meta.state_counts` when `include_test_personas=true` (nothing hidden). `meta.hidden_test_persona_strategy_count` and `meta.hidden_unbound_stub_strategy_count` come from the owner's hidden counts by reason over the filtered candidate set. A Registry that does not answer the hide input is detected by the missing counts and classified by the BFF as before (a walk only when test personas exist), so a rollout in either order stays correct.
- `meta.visible_state_counts` is present on every filter, including the default view (`exclude_state=draft&hide_retired_personas=true`) and any other `state` / `exclude_state` / `persona_id` filter combined with `hide_retired_personas=true` (BFF-STRATEGY-DEFAULT-VIEW-COUNTS-20261125). The owner walk that hide-retired filtering needs sends the same owner hide input (`hide_stubs`, `hide_persona_ids`) as the single-page read and takes `visible_state_counts` and the hidden counts by reason from its first owner page, so no extra owner call is made. The counts describe the rows the owner did not hide (test / stub), before the retired-Persona hide (that filter is reported by `meta.hidden_retired_persona_count`), so the default view's tab counts equal what the matching `state=` filter returns. It stays `null` only when the owner read failed or the owner did not answer the hide input (older Registry: rows are classified by the BFF as before).
- Tenant isolation, `(last_modified_at DESC, strategy_id DESC)` ordering, offset page tokens and the `meta.operational_counts` rules above are unchanged: the counts exist when the candidate set fits one owner page (one extra owner read, so at most two owner calls per request) and are `null` with `meta.surfaces.operational_counts.status: "unavailable"` otherwise. No new writer or recurring job exists.

### `GET /bff/evolution-programs/{program_id}/runs` — retrain run outcome

Each item is read back from the owning plane. For a retrain run the item's
`detail` additionally ends with `; retrain outcome=<candidate_created |
no_candidate | evaluation_rejected> outcome_reason=… candidate_artifact_id=…
session_id=…` once the run recorded an outcome. Existing fields are unchanged.

Each item also carries the structured result as top-level fields, passed
through unchanged from the owning run (Evolution
`GET /api/evolution/programs/{program_id}/runs`, which the BFF returns as is;
nothing is parsed from `detail`):

| Field | Type | Meaning |
|---|---|---|
| `outcome` | string \| null | `candidate_created`, `no_candidate` or `evaluation_rejected` |
| `outcome_reason` | string \| null | the reason the run recorded for the outcome |
| `candidate_artifact_id` | string \| null | the registered candidate child, only for `candidate_created` |
| `baseline_metrics` | object \| null | baseline evaluation metrics, including `sharpe_ratio` and `sharpe_defined` |
| `candidate_metrics` | object \| null | candidate evaluation metrics; null when no candidate was evaluated |
| `session_id` | string \| null | the Teaching session opened for the decision |

A field the run did not record is `null`; a run that recorded none of them
(still queued, or not a retrain) returns all six as `null`. These fields are
never defaulted, so a run is never reported as a successful evolution by them.
The remaining record (`baseline_job_id`, `candidate_job_id`, `baseline_eval_id`,
`candidate_eval_id`) is returned by `GET /api/research-orchestrator/runs/{run_id}`.
A retrain run left `queued` or `running` by a research-orchestrator restart is
resumed in place at startup, so the same item reaches a terminal state.

## Cross-source newest-first job list ordering and pagination (BFF-JOBS-LIST-NEWEST-FIRST-20261106)

- `GET /bff/jobs` orders composed jobs across all qualified sources newest first by `started_at` falling back to `created_at` (normalized to UTC ISO-8601 when parseable), with a deterministic tie break by `id` (`job_id`) and any job with neither `started_at` nor `created_at` sorted last.
- Cross-source ordering is enforced both in `JobReadPort.list_jobs_bff` (`services/control-plane/bff/ports/job_read.py`) and in the `GET /bff/jobs` handler (`services/control-plane/bff/jobs/router.py`) after `status` and `job_type` filtering and before `page_slice(jobs, page_token, page_size)`.
- Walking every page via `page_size` and `next_page_token` returns each matching job exactly once in global newest-first order while `page_info.total` and `meta` source diagnostics (`healthy_sources`, `degraded_sources`, `bff_composed` / single-source owner / `unavailable`) remain unchanged.

## Compact job list rows (BFF-JOBS-LIST-COMPACT-ROWS-20261106)

- `GET /bff/jobs` rows omit the `detail` field (the full source record); every other row field (`job_id`, `id`, `native_id`, `source`, `job_type`, `status`, timestamps, `progress`, `logs`, `allowedActions`, ...) is unchanged. `GET /bff/jobs/{job_id}` still returns `detail` unchanged.
- Ordering, filters, paging and `page_info.total` are unchanged; ordering is computed before `detail` is dropped, so the `detail` timestamp fallback still applies.
- Assistant context collectors read jobs from `read_store.list_jobs_bff()` directly, not from this route, and are unaffected.


### Persona list compact rows (BFF-PERSONA-LIST-COMPACT-ROWS-20261106)

- `GET /bff/personas` rows omit `sourceHealthBindings` / `source_health_bindings` and the `required_source_health` / `requiredSourceHealth` parts of `dataSourceStatus` (about 270 KB each per row on hosted data, three copies per row). Scalar `dataSourceStatus` fields (state, source_health_source, live_ingestion_enabled, live/static connector ids), `dataSources`, `dataSourceRefs` and `requiredDataSources` are unchanged. Each row carries `listScope` (`"current"` | `"test"` | `"retired"` | `"research"`), where `"test"` comes from `classify_persona_data_scope`, `strategy_family == "dev_paper_baseline"`, or a `provisioning_idempotency_key` starting with `RELEASE_CASE_KEY_PREFIX` (`"dev-paper-release-"`), and never from `name`; the default row set, order, and paging are unchanged. `successRate` on persona projections (`GET /bff/personas`, `GET /bff/personas/{persona_id}`, `POST /bff/personas`, `PATCH /bff/personas/{persona_id}`) and `success_rate` on `GET /bff/management/persona-league` rows are `null` (`None`) when no value was recorded or the recorded value is the filled-in `0.0`; create and PATCH write `success_rate` only when the caller sent it, and no win rate is computed. The `items`/`data` envelope is unchanged.
- `GET /bff/personas/{persona_id}` and `GET /bff/management/persona-fleet` keep their current fields; the shared `_project_persona_dto` is unchanged, only the list projection strips.
- `total`, filters, paging and tenant isolation are unchanged.

### Management NL loop_health context surface (BFF-AI-LOOP-HEALTH-20261107)

- `POST /bff/management/nl/ask` and `POST /bff/management/nl/ask/stream` accept focus `loop_health` (aliases `loop`, `loops`, `loop-health`) and include `loop_health` under `focus="all"` (and `""`).
- Both async ask routes prefetch canonical loop health through the existing `ControlLoopsService.loop_health` path (`GET /bff/v5/loop-health`) with the authenticated caller validated by `authenticated_loop_truth_scope` and a bounded wait (`PANTHEON_BFF_MGMT_NL_LOOP_HEALTH_TIMEOUT_SECONDS`, default **6.0 s**, cap 10 s; BFF-AI-LOOP-BUDGET-20261110 raised it from 2.0 s because hosted `GET /bff/v5/loop-health` reads on BFF `a51fd808c` at VM load ~6-8 took 521, 3251, 1151, 466, 392, 674, 392 and 1183 ms over 8 reads, so one of eight exceeded 2.0 s and an ask returned `loop_health` unavailable; an ask takes 15-40 s end to end; a read exceeding the bound still yields `unavailable` with `reason = read_timeout` and never fails the ask) before calling `asyncio.to_thread(_mgmt_nl_collect_context, ...)`. `_mgmt_nl_collect_loop_health` never manufactures a synthetic `OperatorIdentity` or nests an event loop; a context call without an authenticated prefetched outcome reports `unavailable` (`missing_prefetched_loop_health`).
- `summary_context.loop_health` (and `backend.management_nl.data.summary_context.loop_health` delivered to the assistant provider) carries `{status, total, accepted, by_live_status, items}` with one compact row per canonical loop `{loop_id, live_status, current_record_accepted, status, summary}` (`summary` cut to 200 characters and trimmed across rows when needed so the full serialized snippet is <= 8192 bytes for 12 loops in both `ensure_ascii=True` provider JSON and UTF-8 JSON while retaining all 12 canonical rows).
- `meta.surfaces.loop_health` reports `ok` only when `accepted_live` is `True` and all canonical rows are accepted, `degraded` when any row is not accepted, and `unavailable` when the controller health store is unavailable, the read raises, the caller is out of scope, the read times out, or no authenticated prefetched outcome is present; none of these fails the ask.
- `_mgmt_nl_synthesize_answer` reports `<accepted>/<total> canonical loops accepted (<accepted> of <total>)` when available and states that loop health cannot be determined when the surface is `unavailable`.

### Management NL current-state context (BFF-AI-CURRENT-STATE-20261107)

- The Management NL context pack no longer requests a second `persona_health` source (the hardcoded `lifecycle_state == active` rule is deleted). Persona health reaches the provider only through `summary_context.persona_fleet`, which uses the existing `_project_persona_fleet_health` rule; the assistant context-pack route keeps its own `persona_health` collector.
- `_mgmt_nl_collect_context` splits runtime bindings once with the trading pulse retired rule (`_trading_pulse_binding_is_retired`). Retired runtimes are not rows in the cockpit, trading pulse, portfolio or persona fleet sections, cause no telemetry, monitoring or rollback read and no owner observation, and are reported as `retired_runtime_count` (trading pulse `summary`, `portfolio`, `persona_fleet.summary`).
- Retired personas (projected state `retired`) are not projected into `persona_fleet`; they are reported as `summary.retired_persona_count`. Remaining rows are ordered operational first before the 20 row cap, `summary.total_personas` is the non-retired population (so `population_scope` stays correct), `summary.by_lifecycle_state` counts that population, and `draft` / `research_only` personas are not counted in `degraded_personas`.
- Alerts, incidents, anomalies and human inbox rows have no tenant key. For a caller with a tenant scope these rows are treated as deployment scoped (the same caller reads them through `GET /bff/alerts`, `/bff/incidents`, `/bff/management/human-inbox`; the NL route requires the same read role), in `_mgmt_nl_collect_context` and in the assistant `alerts` source collector. A row naming another tenant is still removed, a caller with no tenant scope gets none, and every other source keeps the strict `_mgmt_nl_record_matches_tenant` rule.
- `get_context_incidents` reads with `open_only=True`, so resolved incident history adds no evidence entity and persona `active_incidents` come from open rows.
- `get_context_incidents` reads through `read_open_incidents`, which asks the owner for `open_only` with `raise_on_error`. A successful empty open list is `ok` (a real empty); a failed owner read is `unavailable` and is never read as empty. This applies to the incidents subject only; every other subject keeps its rule that an empty read is `degraded`.
- Persona fleet per-persona reads (BFF-AI-FLEET-EMPTY-OK-20261109): `persona_sessions` and `persona_teaching_sessions` read through `list_persona_sessions` / `list_persona_teaching_sessions`, `persona_bindings` through `read_bindings_for_persona`, and `persona_strategies` through `list_strategy_specs(persona_id=)`, all of which raise on an owner failure. A successful empty read is `ok` (a draft, research-only or paper persona with no such rows); a failed owner read is `unavailable`. A read whose owner is not wired (`OwnerReadUnconfiguredError`), or a store with no raising read, cannot tell an owner failure from an empty owner and keeps the `degraded` rule; the `degradation_reason` names that reader. The swallowing `get_sessions_for_persona`, `get_teaching_sessions_for_persona` and `get_bindings_for_persona` still return `[]` to their other callers.
- `persona_allowed_actions` is scoped by the persona the actions are derived for (the actions carry no tenant). It reports `ok` for a held persona, including an empty action set; `degraded` with "not held by the persona owner" for a persona the owner does not hold or that is outside the caller's tenant; and `unavailable` for an owner read failure or an unavailable persona surface. Allowed-action derivation still reads sessions through the swallowing `get_sessions_for_persona`, so a session read failure can change `canPause` without marking this observation.
- `_mgmt_nl_collect_context` reads the human inbox through `ManagementService.get_human_inbox` with the caller identity, the same builder as `GET /bff/management/human-inbox`. The inbox surface statuses and `human_inbox_summary.total` (taken from `page_info.total`, not the length of one page) equal the route's for the same caller; an approval queue read that succeeded with zero records is `ok`. The governance `_human_inbox_payload` builder and its `wire_management_runtime_projections(human_inbox_payload=...)` wiring were removed.
- Rollback reads are unchanged (owned by BFF-JOURNAL-GOVERNANCE-OWNER-READ-20261106).

### Management NL persona_fleet and human inbox context (BFF-AI-FLEET-INBOX-20261107)

- `POST /bff/management/nl/ask` and `POST /bff/management/nl/ask/stream` run `_mgmt_nl_collect_context` in a worker thread with the authenticated caller identity; both routes receive the same persona_fleet and inbox context.
- persona_fleet projects each persona with the app-scoped `PersonaService` bound as the current service (bound by the `project_persona_fleet_item` callback that `main.py` passes to `wire_management_runtime_projections`), the same binding `PersonaService.build_persona_health_items` uses. No module global or second persona projection is added.
- A persona whose projection raises is reported as an unavailable item with an owner observation `degradation_reason=persona_projection_failed:<ExceptionClass>`; other personas still project. The persona_fleet error branch logs one warning with the exception class.
- The cockpit human inbox count reads `data.items` of the wired human inbox payload and passes the caller identity, so `human_inbox_summary.total` and the `approval_queue` surface follow the inbox read for the same caller.

## Instrument-aware portfolio holdings and performance attribution (INSTRUMENT-AWARE-TELEMETRY-BFF-DRIFT-20261107)

Read-only; no new route. These fields extend the existing `GET /bff/management/portfolio-book/holdings` items and the performance-attribution facts and `summary` from the telemetry runtime summary `positions` rows. A cash share row is unchanged apart from the new fields reading `null` / `1.0`.

### Holdings item fields

| Field | Meaning |
| --- | --- |
| `symbol` | Underlying or native symbol (for a stock future, the underlying, e.g. `2330.TWSE`). |
| `instrument_id` | Traded instrument (`ssf:QFF:202611`, or `eq:2330` when the fill carried it); `null` for a cash share whose fills carried none. |
| `instrument_kind` | `cash_share` or `stock_future`; `null` when the fill reported none. |
| `contract_month` | `YYYYMM` for a stock future, else `null`. |
| `multiplier` | Contract multiplier; `1.0` for a cash share or when none was reported. |
| `margin_reserved` | Initial margin the ledger reserved for the open contracts (currency units); `null` for a cash share. |
| `asset_class` | An explicit value wins; otherwise derived from `instrument_kind` (`stock_future` -> `future`, `cash_share` -> `equity`), else `other`. |
| `holding_id` | `{runtime_id}:{key}`; the key is the position's own id, else the `instrument_id` of a non-cash instrument, else the symbol, so a future never shares an id with its underlying's cash row. |
| `reason_code` | Reason code of the decision that opened or last changed the position (`long_cash`, `long_lever`, ...), folded by the telemetry runtime summary from the `trade_decision` event (`metadata.expression_decision.reason_code`, else `metadata.reason_code`) for the fill's instrument, falling back to the fill's own `reason_code`; `null` when the runtime summary row has none. Served under this snake case key; the frontend reads it as `reasonCode`. |
| `decision_id` | `metadata.decision_id` of the same decision; `null` when none. Attribution facts are unchanged. |

### Valuation

- `market_value` = `quantity` x `multiplier` x `mark_price`, signed by quantity. `unrealized_pnl` is taken from the telemetry row, which computes `quantity` x `multiplier` x (`mark_price` - `average_price`).
- `average_price` is the cost per unit (a share or a contract). Realized PnL is in currency and is scaled by the fill multiplier when the position is reduced.
- When the mark is stale or unavailable (`mark_status` is `stale` or `unavailable`), `mark_price`, `market_value` **and `unrealized_pnl`** are all `null`; only the last known quote provenance (`last_mark_at`, `mark_source_ref`) is kept.
- Expiry settlement (`paper_futures_settled`) closes the contract row and books the ledger's realized PnL; it is not a fill and does not change fill counts.

### Performance attribution

- Each fact carries the same instrument fields and values `market_value` / `notional` with the multiplier.
- `data.summary.instrument_breakdown` is an additive list grouped by traded instrument (`instrument_key` is the `instrument_id`, else the symbol) with `holding_count`, `market_value`, `total_notional`, `total_pnl`, `unrealized_pnl`, `realized_pnl`, `multiplier` and `margin_reserved`. `supported_dimensions` is unchanged.

- `data.summary` carries three additive learning fields that are graded **separately** (INSTRUMENT-DECISION-LEARNING-COUNTERFACTUAL-20261107), read from the `persona_instrument_counterfactual` rows the telemetry runtime summary folds (`instrument_counterfactuals`, keyed `(decision_id, instrument_id)`):
  - `direction_hit_rate`: fraction of graded decisions whose view direction matches the sign of the underlying's next-close move (`underlying_mark - underlying_entry`). It is independent of the instrument chosen. A decision with no underlying prices, a FLAT view or a zero move is not graded; `null` when nothing is graded (never a fabricated 0).
  - `instrument_regret`: sum over decisions of `max(pnl_cf over candidates considered) - pnl_chosen` (`pnl_chosen` is 0 for a recorded no trade), `>= 0`; `null` when no decision can be computed.
  - `margin_use`: sum of the chosen legs' initial margin (0 for a no trade); `null` when no rows exist.
  - `instrument_learning` holds the same grades with `direction_hit_count`, `direction_graded_count`, `instrument_regret_graded_count`, `decision_count` and one entry per decision (`decision_id` is prefixed `{runtime_id}:`; `direction_hit`, `instrument_regret`, `margin_use`, `chosen_instrument_id`, `best_instrument_id`, `chosen_pnl`). A correct call in a worse instrument is therefore a direction hit with positive regret. `pnl_cf = side * q_cf * multiplier * (mark - entry) - costs` with `q_cf` from the same policy sizing rule applied to that candidate, on prices already held (D-1 settlement); no new data source.

- **When grades exist (PAPER-INSTRUMENT-COUNTERFACTUAL-WRITER-20261110).** The paper signal producer writes the rows; the BFF only reads them. A decision made on session `D` priced from the close of the previous session `P` (its `eq:` candidate `price_date`). Its grading session is the first trading session after `P`. The rows are emitted by the first producer bar on which both the underlying close of the grading session (from the admitted snapshot's close series) and the daily settlement of every candidate contract with `price_date` equal to the grading session (from the normalized `taifex_ssf_contracts` dataset) are available. That is normally the first bar after the decision session closes (for a decision made on 2026-11-10 the 2026-11-11 bar); a producer that was down or ran an older build catches the decision up on a later bar while it is still inside the snapshot's close window. One `persona_instrument_counterfactual` row is emitted per candidate considered (the chosen leg flagged) and a decision is graded exactly once. `pnl_cf`, `costs` and `margin_use` come from the same replay rule as the chosen leg.
- **What `null` means.** `direction_hit_rate: null` / `instrument_regret: null` / `margin_use: null` and an empty `instrument_learning.decisions` mean *no decision has been graded yet*, not a miss: the decision is still waiting for its grading session, or it is not gradable. A decision is left ungraded, with no row, when the grading-session underlying close is outside the snapshot window, or when a candidate contract's settlement for the grading session is missing, stale (another session's price) or tombstoned; a price is never reused or fabricated. Only daily-bar artifacts with the expression layer on are graded; legacy artifacts and the flag-off path emit nothing and stay `null`. The reason for a pending or not-gradable decision is carried on the producer's signal metadata (`instrument_counterfactual_grading`) and is not exposed by this endpoint.

### Decision projection

`subject.asset_class` of a projected trading-room decision event follows the chosen instrument kind (`cash_share` -> `equity`, `stock_future` -> `future`); a decision with no instrument stays `equity`, and an unknown kind is rejected instead of being labelled equity.

## Retire a provisioned paper Persona case (BFF-PERSONA-CASE-RETIRE-COMMAND-20261108)

`POST /bff/v1/commands` with `command: "PersonaAction"`, `action: "RetirePaperPersonaCase"` (verb aliases `retire`, `retire_case`) and `target: {"type": "Persona", "id": "<persona_id>"}`. No new HTTP route is added; `POST /bff/personas/{persona_id}/actions/{action_id}` returns `404 RESOURCE_NOT_FOUND` for `retire` and any other unsupported action name (only AdvanceLifecycle, Promote, PromoteCandidate and Demote are live on that route).

Admission is the existing command admission: operator role, `Idempotency-Key` header, non-empty `audit_context.reason`, and an `X-Confirm-Token` issued for `RetirePaperPersonaCase` on that Persona (the catalog entry has `requires_confirm_token`). The tenant is taken from the authenticated claims only.

Execution calls the existing `retire_persona_provisioning_case(tenant_id, persona_id, reason=audit_context.reason)`; the coordinator owns every retirement step (schedule, compensation, saga, runtime bindings, persona capital binding, pool, persona archive).

Receipt (`domain_receipt`, repeated in `authoritative_readback`):

```json
{"persona_id": "persona-…", "state": "retired | <unchanged ledger state>",
 "retirement": {"status": "retired | pending", "pending_step": "…", "pending_reason": "…"}}
```

`pending_step` and `pending_reason` are present only when `retirement.status` is `pending`; replaying the command continues from that step.

Failed receipts (no owner write happens):

| code | downstream_status | meaning |
| --- | --- | --- |
| `persona_case_not_found` | 404 | the Persona has no provisioning ledger |
| `release_case_use_release_retire` | 409 | the ledger idempotency key starts with `dev-paper-release-`; use `scripts/retire_dev_paper_release_cases.py` |
| `tenant_required` | 403 | the authenticated caller has no tenant claim |

Dry run (`dryRun: true`, the existing command-admission mechanism) reads the provisioning record only and never calls the coordinator. The response is the usual dry-run submission with `meta.plan`: `persona_id`, `state`, `release_case`, `retirement_status`, `planned_steps` (steps not yet confirmed, in coordinator order) and `confirmed_steps`. A missing ledger or a release case is refused with the same codes as above.

### PM-12 PnL formula version (2026-11-12)

`GET /bff/management/quarterly-ranking`, its formula/drilldown reads, and persona
league reads publish `pm12-return-on-capital-v2`. PnL is scored as
`clamp(50 + 100 * (pnl / capital), 0, 100)`; the existing four component weights
remain unchanged. Formula metadata includes the previous `pm12-default-v1`
version and governance reference `pm12-quarterly-ranking-formula-v2-operator-20261112`.
The operator decision is recorded in
`docs/deployment/evidence/RANKING-PNL-NORMALISATION-20261112/operator-decision.md`.

Each item's `metrics` records `pnl_capital` (positive capital denominator),
`pnl_unit=ledger_currency`, `pnl_return` and `pnl_return_unit=fraction`,
`pnl_period` (`start_at`, `as_of`, `quarter`), `pnl_return_status`,
`pnl_return_reason`, and `pnl_sources` (runtime, capital source, amount, optional
currency and event ID). The denominator is the same PnL observation's
`initial_cash`, never a persona budget or default. Projection carries capital
and `pnl_period_start_at` with PnL atomically, clears them on binding rollover
or a newer PnL without provenance, and retains them across unrelated heartbeats.

The quarterly PnL period must lie inside `quarter_window`. A ledger opened
within the quarter supplies a since-inception quarter-to-date return; older
ledgers remain unknown until owner-backed quarter opening equity is available.
Repeated cumulative telemetry observations are not added together for return
scoring. Multiple runtimes require complete observations with matching period
and currency; an incomplete or incompatible cohort stays unknown. Other score
components retain their existing latest-telemetry semantics.

When PnL/capital/period is unknown, `components.pnl_score`, `overall_score`,
`score`, `rank`, and `allocation_policy_input` are null, `tier=unknown`,
`eligible=false`, and `exclusion_codes` includes `unknown_pnl_return`. Drilldown
keeps unknown scores/contributions null. Zero PnL with valid capital scores 50.
Historical v1 snapshots keep their stored values and digest; the allocation
input adapter and durable snapshot reader accept both formula versions and
reject unsupported versions.

## Publish a persona strategy-pool version (POOL-ENVELOPE-PUBLISH-20261112)

`POST /bff/personas/{persona_id}/strategy-pool/publish` — operator role. Body (optional): `{"pool_version": <int>, "stage": "paper"}`; omitted `pool_version` publishes the current pool version. Any other field is `422`.

Each pool version becomes one new immutable envelope artifact in the existing Registry: it pins every non-removed member's version, checksum and weight plus `pool_version`/`pool_digest`, with lineage to the previous envelope. Approval (automated, paper scope), the DeploymentPlan and the runtime binding move all use the existing Registry / Governance / Deployment owner paths; the BFF never calls RuntimeManager. Deployment moves the binding with the existing runtime-manager `replace` route, so the persona keeps one capital pool, one runtime, and its positions and ledger.

Responses: `200 {data: {status: "published"|"dispatched", pool_version, envelope_artifact_id, envelope_version, approval_decision_id, deployment_plan_id, deployment_saga_id, source_binding_id, runtime_id, capital_pool_id, members[], idempotent_replay}}`. `published` means a separate RuntimeBinding readback shows the new envelope live; `dispatched` means Deployment admitted it but the binding has not moved yet (re-publish to reconcile). Publishing the same pool version again is idempotent.

Fail-closed `409` (reason recorded in the publish history, previous envelope keeps running): member not approved / checksum mismatch / other-tenant artifact, pool digest differs from an already published snapshot, pool version not newer than the running envelope, not exactly one live RuntimeBinding, owner-chain readback mismatch. `403`: any stage other than `paper`. `404`: no pool. `503`: owner unavailable.


### Meetings execution truth (CONSULT-MEETING-RESEARCH-TRUTH-20261120)

- Consultation `GET /api/consult/meetings` and `/{meeting_id}` attach `execution_state` (`status`, `attempt_count`, `max_attempts`, `last_error`, `updated_at`, matched by workflow `request_id` == `meeting_id` within the caller tenant, one tenant-scoped read per list) and `effective_status`. A triggered/convened round-0 meeting whose workflow is `dead_letter` has `effective_status = "execution_failed"` and is excluded from `status=upcoming` unless `include_failed=true`; `summary.dead_letter_count` reports how many were excluded.
- BFF meeting list/detail pass through `execution_state`, `effective_status`, and `summary.dead_letter_count` (list `data.summary.dead_letter_count`; `open_count` excludes `execution_failed`). `MeetingsOwnerClient.list_meetings(include_failed=...)` forwards the flag; the router does not yet expose it as a query parameter.
- Meeting rows/detail add `topic_summary` = `{kind, subject_persona_id, period, lesson_count}` parsed from `Pool evolution review for persona-... (YYYY-Www)` (`pool_evolution_review`) and `Optimisation review for persona-...: lessons:N` (`optimisation_review`); `null` for free-text topics. `topic` is unchanged.

### Shared Persona OODA owner binding for packet detail and case-file reads (BFF-DECISION-CASE-FILE-OWNER-READ-20261121)

- `AppDependencies.create_default` and `create_read_surface_ports` wire `OodaManagementDomainPort` with `create_ooda_packets_port()` (`OodaPacketsPort`), which binds the existing read-only Persona OODA owner decoder (`resolve_ooda_packet_store_path()` + `read_packets(path)`) when `PANTHEON_BFF_OODA_PACKET_STORE`, `PANTHEON_OODA_DATA_DIR`, or `PANTHEON_CONTROL_PLANE_DATA_DIR` is configured and no explicit `store` or `records_provider` is injected.
- `personas/cycle_views.py` (`read_cycle_records`), `ports/persona_pool.py` (`PersonaPoolViewsPort.read`), `GET /bff/ooda/packets`, and `GET /bff/ooda/packets/{packet_id}` all resolve through the single shared `read_store.ooda_management.ooda` port, so every paper daily-cycle packet ID surfaced on `GET /bff/personas/{persona_id}` and `GET /api/v1/operator/persona-management/{persona_id}` resolves on `GET /bff/ooda/packets/{packet_id}` with `data.case_file_view` (`project_packet(data)`) and raw `persona_cycle.case_files` stripped.
- `ReadSurfacePorts.dataset_source("ooda_packets")` reflects `ooda_management.ooda.get_surface_status()["source"]` (`"store"` / `"service"` when readable, `"missing"` when unconfigured or absent, `"unavailable"` when unreadable), preserving `meta.surfaces.ooda_packets` and `meta.surfaces.ooda_packet_detail` degradation on absent/corrupt stores and `404 RESOURCE_NOT_FOUND` on genuine missing or cross-tenant/non-paper packets.

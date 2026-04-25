# APP-001C Query Contract Outline

Last updated: 2026-04-10
Status: draft — BFF query contract outline for APP-001
Tier: L2 Planning & Execution (input for APP-001)
Scope: Request/response shapes, query parameter envelope, error contract, staleness model, and versioning contract
Derived from: APP-001A BFF_SURFACE_INVENTORY.md + APP-001B OWNER_PACKET.md §2 (Query Contract Critique) + APP_001C_DESIGN_SKELETON.md
Reviewer: Codex

---

## 1. Purpose

This document outlines the **query contract** for the governed BFF (APP-001). It addresses the 7 contract gaps identified in APP-001B §2 and provides the structural specification that the APP-001 owner will implement as a formal OpenAPI/JSON Schema contract.

It covers:
- Standard query parameter envelope (§2)
- Standard response envelope (§3)
- Error response contract (§4)
- Staleness and degradation model (§5)
- Composed view contract (§6)
- API versioning contract (§7)
- Real-time feed contract (future, §8)

---

## 2. Query Parameter Envelope

### 2.1 Standard List Query

All list surfaces share this baseline query envelope:

```
GET /api/v1/{resource}
  ?page=<int>                  # default: 1, min: 1
  &page_size=<int>             # default: 20, min: 1, max: 100
  &sort=<field>                # default: resource-specific primary key
  &sort_dir=<asc|desc>         # default: asc
  &filter.<field>=<value>      # resource-specific filterable fields
  &filter.<field>.op=<eq|ne|gt|gte|lt|lte|in|contains>  # default: eq
```

**Response**: Returns a paginated list with metadata.

### 2.2 Surface-Specific Query Parameters

Each surface extends the standard envelope with resource-specific parameters:

| Surface | Additional Parameters | Notes |
|---|---|---|
| PS-01 (Persona List) | `filter.lifecycle_state=`, `filter.mandate=` | Lifecycle state filter |
| TL-01 (Telemetry Query) | `filter.time_range.start=`, `filter.time_range.end=`, `filter.pool_id=`, `filter.artifact_id=` | Time range in RFC 3339; max window: 30 days |
| TL-02 (Telemetry Summary) | `filter.time_range.start=`, `filter.time_range.end=`, `aggregate_by=<hour|day|week>` | Aggregation granularity |
| LN-03 (Lineage Graph) | `filter.root_type=`, `filter.root_id=`, `depth=<int>` | Max depth: 10 |
| IN-01 (Incident List) | `filter.status=`, `filter.severity=`, `filter.affected_pool_id=` | Status: active/resolved/closed |
| DP-01 (Deployment Plan List) | `filter.stage=`, `filter.target_pool_id=` | Stage: candidate/paper/canary/live |
| RT-04 (Rollback History) | `filter.runtime_id=`, `filter.action_type=`, `filter.time_range.start=`, `filter.time_range.end=` | Action type: replace/pause_then_replace/liquidate_then_replace |

### 2.3 Pagination Defaults and Maximums

| Parameter | Default | Min | Max | Notes |
|---|---|---|---|---|
| `page` | 1 | 1 | — | No upper bound (returns empty page if beyond data) |
| `page_size` | 20 | 1 | 100 | Surfaces with high-cardinality data may enforce lower max |
| `depth` (LN-03) | 3 | 1 | 10 | Prevents unbounded graph traversal |
| Time range window (TL-01) | — | — | 30 days | Requests exceeding max return HTTP 422 |

### 2.4 Filterable Fields Policy

Not all fields on a resource are filterable. The contract adopts an **opt-in filterable field list** per surface, defined in the formal API schema. Fields not on the allowlist return HTTP 400 with `UNKNOWN_FILTER_FIELD`.

Filter operators supported:
- `eq` (default): Exact match
- `ne`: Not equal
- `gt`, `gte`, `lt`, `lte`: Comparison (numeric and date fields only)
- `in`: Comma-separated list of values
- `contains`: Substring match (string fields only, case-insensitive)

---

## 3. Response Envelope

### 3.1 List Response

```json
{
  "data": [
    { ... resource object ... },
    { ... resource object ... }
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

### 3.2 Detail Response

```json
{
  "data": {
    ... single resource object ...
  },
  "meta": {
    "staleness": null
  }
}
```

### 3.3 Composed View Response

```json
{
  "data": {
    "deployment_plan": { ... },
    "capital_pool": { ... },
    "bindings": [ ... ],
    "runtime_binding": { ... },
    "rollbacks": [ ... ]
  },
  "meta": {
    "snapshot_at": "2026-04-10T03:00:00Z",
    "surfaces": {
      "deployment_plan": { "status": "ok" },
      "capital_pool": { "status": "ok" },
      "bindings": { "status": "ok" },
      "runtime_binding": { "status": "degraded", "staleness": { "served_from": "cache", "last_known_at": "2026-04-10T02:30:00Z" } },
      "rollbacks": { "status": "unavailable" }
    }
  }
}
```

### 3.4 Resource Object Shape

Each resource object in `data` follows this pattern:

```json
{
  "id": "<resource_id>",
  "type": "<resource_type>",
  ... canonical fields from L1 object ...
  "_links": {
    "self": "/api/v1/{resource}/{id}",
    "related": { ... related resource links ... }
  }
}
```

The `_links` section follows HATEOAS principles, allowing the client to navigate related surfaces without hardcoding URL patterns.

---

## 4. Error Response Contract

### 4.1 Standard Error Shape

```json
{
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<Human-readable message>",
    "details": {}
  }
}
```

### 4.2 Error Code Catalog

| Error Code | HTTP Status | Description |
|---|---|---|
| `INVALID_REQUEST` | 400 | Request body or query parameter validation failed |
| `UNKNOWN_FILTER_FIELD` | 400 | Filter field not in allowlist for this surface |
| `INVALID_FILTER_VALUE` | 400 | Filter value does not match expected type |
| `INVALID_TIME_RANGE` | 422 | Time range exceeds maximum window or is inverted (start > end) |
| `PAGINATION_OUT_OF_RANGE` | 422 | Page number or page_size outside allowed bounds |
| `UNAUTHORIZED` | 401 | Authentication required or token invalid |
| `FORBIDDEN` | 403 | Operator lacks required role for this surface |
| `NOT_FOUND` | 404 | Resource ID does not exist |
| `DOWNSTREAM_UNAVAILABLE` | 503 | Downstream service unreachable |
| `DOWNSTREAM_TIMEOUT` | 504 | Downstream service request timed out |
| `INTERNAL_ERROR` | 500 | Unexpected BFF error |

### 4.3 Degradation Is Not an Error

When a surface returns degraded data (stale or partial), or when a composed view contains unavailable sub-surfaces, the BFF returns HTTP 200 with appropriate staleness metadata — **not** an error response. This allows the UI to render partial pages without masking which downstream surfaces degraded.

Error responses are reserved for:
- Client-side validation failures (4xx)
- BFF internal failures (5xx)
- Single-surface endpoints with no verifiable payload because the downstream service is unavailable (503)

List endpoints may return stale or replica-backed data with metadata rather than errors when the downstream is unavailable. If no verifiable list payload is available, they must return `DOWNSTREAM_UNAVAILABLE` rather than an empty list that could be misread as "none".

### 4.4 Time Range Validation

For surfaces accepting `filter.time_range`:
- Both `start` and `end` are required when time_range is specified
- Both must be valid RFC 3339 timestamps
- `start` must be before `end` (inverted ranges return HTTP 422)
- The window (`end - start`) must not exceed the surface-specific maximum (TL-01: 30 days)

---

## 5. Staleness and Degradation Model

### 5.1 Staleness States

| State | Meaning | `served_from` Value |
|---|---|---|
| `null` | Fresh data from primary service | — |
| `degraded` | Data served from read-replica with performance note | `"read-replica"` |
| `stale` | Data served from cache with known staleness | `"cache"` |
| `partial` | Data reconstructed from direct object references | `"reconstructed"` |
| `unavailable` | No data available at all | — |

### 5.2 Surface-Level Staleness

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

### 5.3 Composed View Surface-Level Staleness

```json
{
  "meta": {
    "surfaces": {
      "runtime_binding": {
        "status": "degraded",
        "staleness": {
          "served_from": "cache",
          "last_known_at": "2026-04-10T02:30:00Z"
        }
      }
    }
  }
}
```

### 5.4 Field-Level Staleness (Target State)

```json
{
  "data": {
    "persona_id": "p-001",
    "lifecycle_state": "active",
    "current_binding": {
      "_value": "b-042",
      "_staleness": {
        "served_from": "cache",
        "last_known_at": "2026-04-10T02:30:00Z"
      }
    }
  }
}
```

Field-level staleness is deferred to the formal API contract implementation phase. The surface-level model is the v1 baseline.

### 5.5 "Never Show None" Rule

Per APP-001A §5.1 and BFF_HA §5, the BFF must never return `"data": []` or `"data": null` as a result of a downstream service failure. Instead:
- List surfaces: Return last-known or replica-backed entries with `meta.staleness` when available; otherwise return HTTP 503 with `DOWNSTREAM_UNAVAILABLE`
- Detail surfaces: Return HTTP 503 with `DOWNSTREAM_UNAVAILABLE` if the specific resource cannot be verified
- Composed views: Return partial data with `meta.surfaces` metadata showing which surfaces are unavailable

---

## 6. Composed View Contract

### 6.1 Composed View Endpoints

| Endpoint | Composes | Primary Use Case |
|---|---|---|
| `GET /api/v1/operator/deployment-review/{plan_id}` | DP-02, CP-02, CP-04, RT-02, RT-04 | Pre-deployment approval review |
| `GET /api/v1/operator/incident-response/{incident_id}` | IN-02, RT-03, TL-02, RT-04, EV-04, IN-05 | Active incident response |
| `GET /api/v1/operator/post-incident-review/{incident_id}` | IN-04, EV-01, EV-02, LN-01, TL-03 | Post-incident analysis |
| `GET /api/v1/operator/persona-management/{persona_id}` | PS-02, CP-03, CP-04, PS-03, PS-05 | Persona lifecycle management |

### 6.2 Composed View Query Parameters

Composed views accept the same pagination parameters as individual surfaces, plus:

```
GET /api/v1/operator/deployment-review/{plan_id}
  ?snapshot=preferred    # "preferred" (default): try for point-in-time consistency
                         # "best_effort": accept data from different timestamps
```

### 6.3 Point-in-Time Snapshot Guarantee

When `snapshot=preferred`:
- The BFF attempts to fetch all surfaces within a narrow time window
- The `snapshot_at` timestamp in the response indicates the reference time
- Surfaces fetched outside the window carry a staleness marker relative to `snapshot_at`
- If too many surfaces fall outside the window, the BFF returns the data with appropriate degradation metadata (not an error)

---

## 7. API Versioning Contract

### 7.1 Version Format

URL-based versioning: `/api/v{version}/...`

Current version: `v1`

### 7.2 Versioning Policy

| Change Type | Version Impact | Examples |
|---|---|---|
| Add optional field to response | No version bump | Add `teaching_count` to PS-02 |
| Add new surface | No version bump | Add FB-01 when feedback enters scope |
| Add optional query parameter | No version bump | Add `filter.severity=` to IN-01 |
| Remove field from response | Major version bump | Remove deprecated field |
| Change field type | Major version bump | `id` from string to integer |
| Change required field to optional | No version bump | Relax validation |
| Change authentication model | Major version bump | Token format change |

### 7.3 Deprecation Policy

- Deprecated API versions receive a `Deprecation` response header
- Minimum 90-day notice before version removal
- Deprecation timeline must align with canonical object evolution process

---

## 8. Real-Time Feed Contract (Deferred Design)

### 8.1 Reserved Subscription Surfaces

| Subscription | Data Stream | Reconnection |
|---|---|---|
| `SUB /api/v1/runtime/{runtime_id}/events` | Runtime state changes | Last event ID via `?last_event_id=` |
| `SUB /api/v1/incidents/stream` | Active incident events | Last event ID via `?last_event_id=` |
| `SUB /api/v1/kill-switch/updates` | Kill-switch state changes | Last event ID via `?last_event_id=` |

### 8.2 Stream Event Shape

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

### 8.3 Transport Mechanism (Open Question)

The transport mechanism (SSE vs WebSocket) is tracked in OPEN_QUESTIONS.md. The event shape above is transport-agnostic.

---

## 9. Surface-Specific Contract Details

### 9.1 Persona Surfaces (PS-01–PS-06)

| Surface | Response Shape | Filterable Fields |
|---|---|---|
| PS-01 | `{ data: [Persona], meta }` | `lifecycle_state`, `mandate`, `strategy_family` |
| PS-02 | `{ data: Persona + bindings[], meta }` | — |
| PS-03 | `{ data: [SessionPersona], meta }` | `status` (active/closed) |
| PS-04 | `{ data: SessionPersona + capabilities, meta }` | — |
| PS-05 | `{ data: [TeachingSession], meta }` | `status`, `time_range` |
| PS-06 | `{ data: CapabilitySnapshot, meta }` | — |

### 9.2 Capital Pool Surfaces (CP-01–CP-04)

| Surface | Response Shape | Filterable Fields |
|---|---|---|
| CP-01 | `{ data: [CapitalPool], meta }` | `status`, `risk_policy_ref` |
| CP-02 | `{ data: CapitalPool + bindings[], meta }` | — |
| CP-03 | `{ data: [PersonaCapitalBinding], meta }` | `persona_id`, `capital_pool_id`, `role`, `validity` |
| CP-04 | `{ data: PersonaCapitalBinding + persona, meta }` | — |

### 9.3 Runtime Surfaces (RT-01–RT-04)

| Surface | Response Shape | Filterable Fields |
|---|---|---|
| RT-01 | `{ data: [RuntimeBinding], meta }` | `deployment_mode`, `version` |
| RT-02 | `{ data: RuntimeBinding + deployment_plan, meta }` | — |
| RT-03 | `{ data: RuntimeBinding, meta }` | — |
| RT-04 | `{ data: [RollbackRecord], meta }` | `runtime_id`, `action_type`, `time_range` |

### 9.4 Telemetry Surfaces (TL-01–TL-03)

| Surface | Response Shape | Filterable Fields |
|---|---|---|
| TL-01 | `{ data: [TelemetryEvent], meta }` | `pool_id`, `artifact_id`, `time_range` |
| TL-02 | `{ data: TelemetrySummary, meta }` | `time_range`, `aggregate_by` |
| TL-03 | `{ data: PerformanceChart, meta }` | `artifact_id`, `time_range` |

---

## 10. Verification Checklist

| APP-001B Critique Gap | Addressed In | Status |
|---|---|---|
| §2.1 Missing query parameters & filtering | §2 Query Parameter Envelope | ✅ |
| §2.2 No response shape contract | §3 Response Envelope | ✅ |
| §2.3 Composed view ambiguity | §6 Composed View Contract | ✅ |
| §2.4 Real-time feed gap | §8 Real-Time Feed Contract (deferred mechanism) | ✅ |
| §2.5 Authorization & RBAC not referenced | DESIGN_SKELETON.md §8 (RBAC matrix in open questions) | ✅ |
| §2.6 Versioning & compatibility | §7 API Versioning Contract | ✅ |
| §2.7 Degraded-path field-level granularity | §5.4 Field-Level Staleness (target state) | ✅ |

---

*End of APP-001C Query Contract Outline*

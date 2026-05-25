# BFF Canonical Path and Field Naming Decisions

Status: canonical decision record
Date: 2026-05-25
Sprint: Sprint BFF-DELTA-V3 / EPIC-BFF-DELTA-V3-INFRA
Task: OPS-DOC-BFF-NAMING-CANONICAL-001
Owner: Claude
Reviewer: Codex

This document records canonical naming decisions for the Pantheon BFF layer. It covers five FE/BE
path and query parameter naming alignment decisions, and twelve response-body snake_case duplicate
field patterns. It is an execution record, not a new L1 product authority.

---

## Section 1 — FE/BE Naming Alignment Decisions

These five decisions govern how the TypeScript (FE) and Python FastAPI (BE) naming conventions meet
at the HTTP boundary. The canonical rule for each case is stated once and applies to all affected
routes.

### A1 — URL Path Segments: kebab-case Canonical

**Decision:** All BFF URL path segments use kebab-case. TypeScript builder function names use
camelCase as a TypeScript convention, not as an HTTP path convention.

| FE builder name | Canonical URL segment |
|---|---|
| `managementPortfolioBook` | `/bff/management/portfolio-book` |
| `managementPerformanceAttribution` | `/bff/management/performance-attribution` |
| `managementPersonaLeague` | `/bff/management/persona-league` |
| `managementQuarterlyRanking` | `/bff/management/quarterly-ranking` |
| `managementCapitalFlow` | `/bff/management/capital-flow` |
| `managementStrategyAllocation` | `/bff/management/strategy-allocation` |
| `managementRiskRadar` | `/bff/management/risk-radar` |
| `managementHiqBacklog` | `/bff/management/hiq-backlog` |
| `managementInterventionStream` | `/bff/management/intervention-stream` |
| `managementIncidentTimeline` | `/bff/management/incident-timeline` |
| `managementCostAttribution` | `/bff/management/cost-attribution` |
| `managementGovernanceLedger` | `/bff/management/governance-ledger` |

**Rule:** FE may use any TypeScript-idiomatic name for the path builder; the generated URL must
always use kebab-case. No route shall use snake_case or camelCase in URL path segments.

### A2 — URL Path Parameters: snake_case Canonical in FastAPI Route Declarations

**Decision:** FastAPI route declarations use `{resource_id}` (snake_case) as the canonical path
parameter name. This is a BE-internal naming convention; the HTTP path shape is identical regardless
of the parameter variable name.

Affected route families:
- `/bff/strategies/{strategy_id}` and sub-routes
- `/bff/personas/{persona_id}` and sub-routes
- `/bff/evolution-programs/{program_id}` and sub-routes
- `/bff/jobs/{job_id}` and sub-routes

Both `{strategy_id}` (primary) and `{strategyId}` (legacy alias registered for the evolution
programs family at route registration time) resolve to the same path shape. Callers always supply
the raw ID value; the parameter variable name is invisible to the HTTP client.

**Canonical:** snake_case in route declarations (`{strategy_id}`, `{persona_id}`, etc.).
Legacy camelCase declarations (`{programId}`, `{jobId}`) are retained for backwards compatibility
only and must not be introduced for new routes.

### A3 — Query Parameter: `persona_id` vs `personaId` in `intervention-stream`

**Decision:** `persona_id` is canonical. `personaId` is accepted as a FE-compatibility alias.

Route: `GET /bff/management/intervention-stream`

```python
persona_id: Optional[str] = Query(default=None),  # canonical
personaId: Optional[str] = Query(default=None),   # FE-compatibility alias; maps to persona_id
```

The BE coalesces: `effective = persona_id or personaId`.

**Rule for new routes:** use snake_case query parameters as the canonical form. If a route is
consumed by the FE TypeScript layer and the FE sends camelCase keys, add the camelCase form as an
explicit `Query` alias; do not silently break on either form.

### A4 — Query Parameter: `window_hours` vs `windowHours` in `intervention-stream`

**Decision:** `window_hours` is canonical. `windowHours` is accepted as a FE-compatibility alias.

Route: `GET /bff/management/intervention-stream`

```python
window_hours: int = Query(default=24, ge=1, le=720),        # canonical
windowHours: Optional[int] = Query(default=None, ge=1, le=720),  # FE-compatibility alias
```

The BE coalesces: `effective = windowHours or window_hours`.

**Rule:** time-window parameters always use `snake_case_seconds` or `snake_case_hours` as the
canonical form. camelCase aliases are added only when a FE caller is confirmed to send them.

### A5 — source_type Dual Accept: `source_type` vs `sourceType` in command-executor payloads

**Decision:** `source_type` is canonical. `sourceType` is extracted by the command executor as a
compatibility alias when reading request body fields.

Location: `_extract_command_params` in `services/control-plane/bff/main.py` (command executor).

```python
source_type = str(params.get("source_type") or params.get("sourceType") or "").strip()
params["source_type"] = source_type
params["sourceType"] = source_type  # propagate back for any legacy reader
```

**Rule:** canonical request-body field names use snake_case. The executor may accept camelCase
aliases at parsing time; it must normalize to snake_case before passing to downstream handlers.

---

## Section 2 — snake_case Duplicate Response Fields

These twelve response body fields appear in BFF responses in both camelCase and snake_case form.
The dual form is intentional: the camelCase form serves FE TypeScript callers, the snake_case form
serves BE-internal consumers and any caller using standard REST serializers.

The canonical (authoritative) form is listed first; the alias form is the compatibility duplicate.

| # | Canonical (snake_case) | Alias (camelCase) | Route family |
|---|---|---|---|
| 1 | `runtime_id` | `runtimeId` | trading-pulse, strategy-allocation, capital-flow, risk-radar |
| 2 | `deployment_stage` | `deploymentStage` | trading-pulse, capital-flow |
| 3 | `runtime_binding_id` | `runtimeBindingId` | trading-pulse baseline comparison |
| 4 | `paper_live_drift` | `paperLiveDrift` | trading-pulse baseline comparison |
| 5 | `drift_groups` | `driftGroups` | trading-pulse baseline comparison |
| 6 | `threshold_evaluation` | `thresholdEvaluation` | trading-pulse baseline comparison |
| 7 | `inbox_type` | `inboxType` | management human-inbox items |
| 8 | `source_dataset` | `sourceDataset` | human-inbox, intervention-stream |
| 9 | `risk_level` | `riskLevel` | human-inbox, HIQ backlog |
| 10 | `source_record` | `sourceRecord` | human-inbox items |
| 11 | `stream_sequence` | `streamSequence` | intervention-stream event ordering |
| 12 | `management_href` | `managementHref` | evidence ref links in management routes |

### Duplication Policy

1. Both forms carry the **same value** in every response that emits them. There is no semantic
   difference between `runtime_id` and `runtimeId` in the same response object.
2. FE TypeScript callers should prefer the camelCase form; BE Python callers should prefer the
   snake_case form; either form is stable.
3. Adding a new dual field requires explicit review. The reviewer must confirm that the field is
   consumed by both FE and BE callers in the same response object before approving dual emission.
4. Removing either form of a dual field is a breaking change and requires a deprecation notice in
   the route's spec entry.

### Additional dual fields present in trading-pulse metrics subobject

The trading pulse drift metric counters follow the same dual-emit pattern:

| Canonical | Alias | Context |
|---|---|---|
| `metric_count` | `metricCount` | trading-pulse drift summary |
| `breached_metric_count` | `breachedMetricCount` | trading-pulse drift summary |
| `watch_metric_count` | `watchMetricCount` | trading-pulse drift summary |
| `by_status` | `byStatus` | management analytics aggregates |
| `by_type` | `byType` | management analytics aggregates |
| `created_at` | `createdAt` | human-inbox, HIQ backlog items |

These six additional fields follow the same duplication policy above and are documented here for
completeness; they were not in scope for the v3 delta spec acceptance criteria.

---

## Section 3 — Migration Guidance

### FE callers

- TypeScript `paths.ts` builders generate correct kebab-case URLs. No change needed.
- When calling a management route that accepts dual query params, use the snake_case form as the
  canonical query key. The camelCase form is a compatibility alias only and may be removed in a
  future sprint once FE callers are confirmed to use snake_case.
- When reading response fields, prefer the camelCase form (e.g., `row.runtimeId`). The snake_case
  alias (`row.runtime_id`) is also stable but is intended for BE-internal use.

### BE implementors

- New routes must use snake_case for all query parameters.
- New routes must not introduce camelCase path parameters (`{strategyId}`, `{personaId}`, etc.).
- Dual response fields are only appropriate when the route is known to be consumed by both FE and
  BE callers in the same request context. Prefer snake_case only for new routes unless a confirmed
  FE caller requires camelCase.
- When adding a dual field, add the snake_case key first, then the camelCase alias on the next
  line, with a comment explaining the dual emit.

### Acceptance for new routes

New routes submitted under EPIC-BFF-DELTA-V3-INFRA or any successor EPIC must:

1. Use kebab-case URL path segments.
2. Use snake_case path parameters in FastAPI route declarations.
3. Use snake_case query parameters as canonical; accept camelCase only when a confirmed FE caller
   is identified.
4. Include dual response fields only for fields listed in Section 2 or for fields that gain
   explicit review approval citing this document.

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

### BFF-INFRA-PATH-DEDUPE-001 action log

Timestamp: `2026-05-25T08:40:02Z`

This task applied the same naming boundary to duplicate route registrations. Path parameter
duplicates keep the snake_case FastAPI declaration only. Alternate URL shapes that FE no longer
uses now return HTTP `410 Gone` with `X-Deprecated: true`, `X-Deprecated-At`, and
`X-Pantheon-Replacement-Route`.

| Family | Canonical kept | Removed or deprecated alternate |
|---|---|---|
| `personas` | `/bff/personas/{persona_id}` | Removed legacy `/bff/personas/{id}` declaration; `/bff/personas/{persona_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/persona/{persona_id}/{action_id}`. |
| `pools` | `/bff/capital-pools/{pool_id}` | Removed legacy `/bff/capital-pools/{id}` declaration; `/bff/capital-pools/{pool_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/capitalPool/{pool_id}/{action_id}`. |
| `deployments` | `/bff/deployments/{deployment_id}` | Removed legacy `/bff/deployments/{id}` declaration; `/bff/deployments/{deployment_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/deployment/{deployment_id}/{action_id}`. |
| `rebalances` | `/bff/rebalances/{rebalance_id}` | Removed legacy `/bff/rebalances/{id}` declaration; `/bff/rebalances/{rebalance_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/rebalance/{rebalance_id}/{action_id}`. |
| `incidents` | `/bff/incidents/{incident_id}` | Removed legacy `/bff/incidents/{id}` declaration; `/bff/incidents/{incident_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/incident/{incident_id}/{action_id}`. |
| `runtimes` | `/bff/runtimes/{runtime_id}` | Removed legacy `/bff/runtimes/{id}` declaration; `/bff/runtimes/{runtime_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/runtime/{runtime_id}/{action_id}`. |
| `skills` | `/bff/skills/{skill_id}` | Removed legacy `/bff/skills/{id}` declaration; `/bff/skills/{skill_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/skill/{skill_id}/{action_id}`. |
| `tools` | `/bff/tools/{tool_id}` | Removed legacy `/bff/tools/{id}` declaration; `/bff/tools/{tool_id}/actions/{action_id}` returns 410 in favor of `/bff/actions/tool/{tool_id}/{action_id}`. |
| `mcp-servers` | `/bff/mcp-servers`, `/bff/mcp-servers/{server_id}`, `/bff/mcp-servers/{server_id}/import-tools` | `/bff/mcp/servers` and `/bff/mcp/servers/{server_id}` return 410; duplicate `/bff/mcp-servers/{id}/import-tools` declaration was removed. |
| `mcp-tools` | `/bff/mcp-tools`, `/bff/mcp-tools/{tool_id}`, `/bff/mcp-tools/{tool_id}/{action}` | `/bff/mcp/tools/{tool_id}/actions/{action_id}` returns 410. |
| `ranking-formulas` | `/bff/ranking-formulas`, `/bff/ranking-formulas/{formula_id}` | `/bff/ranking/formulas`, `/bff/ranking/formulas/{formula_id}`, and their write/action sub-routes return 410. |
| `strategy actions` | `/bff/actions/strategy/{strategy_id}/{action_id}` through the generic action route | `/bff/strategies/{strategy_id}/actions/{action_id}` returns 410. |

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

---

## Section 4 — V3 Acceptance-Scoped Route Naming Decisions

This section closes the naming decisions for the specific v3 delta route segments that were raised
in the Codex review of PR #558. It supplements Sections 1–3, which govern the generic FE/BE naming
convention layer. The decisions here operate at the route-segment identity level: each row names
the canonical URL segment, states its scope, and gives explicit keep/deprecate/both guidance.

### Pack D Cross-Reference

The routes in this section belong to the **Pack D — B3 P1 Management Aggregate APIs** task family,
defined in:

- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` §B3 (B3-002
  through B3-017): persona-fleet, human-inbox, trading-pulse, evolution-journal, evidence, and the
  portfolio-book composition suite.
- `docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md` DELTA-5 and
  DELTA-6 (§D2 delta additions): portfolio-book/positions and portfolio-book/exposure.

These cross-references are the authoritative source for route contracts and composition sources.
This section adds only the naming-decision layer on top of those specs.

### D1 — `persona-fleet` vs `persona-league`: Two Distinct Routes

**Decision:** `persona-fleet` and `persona-league` are **not competing names** for the same
resource. They are two separate management surfaces with separate URL paths, separate composition
sources, and separate task origins. No deprecation applies to either.

| Route segment | URL | Task origin | Scope |
|---|---|---|---|
| `persona-fleet` | `/bff/management/persona-fleet` | BFF-B3-002 | Persona binding + runtime state + telemetry + trainer/evolution info aggregate |
| `persona-league` | `/bff/management/persona-league` | BFF-PM12-004 / BFF-B3.4 | Persona rankings + scoring + tiers + movers |

Sub-routes for `persona-league`:
- `/bff/management/persona-league/movers` (BFF-MGMT-DELTA-001)
- `/bff/management/persona-league/rankings`
- `/bff/management/persona-league/tiers`
- `/bff/management/persona-league/heatmap`

**Guidance:**

| Form | Status | Rule |
|---|---|---|
| `persona-fleet` | **KEEP** | Canonical. Use for fleet binding/runtime/telemetry aggregate. |
| `persona-league` | **KEEP** | Canonical. Use for ranking/scoring surface. |

Both routes are canonical, active, and must coexist. A caller requesting fleet state uses
`/bff/management/persona-fleet`; a caller requesting ranking state uses
`/bff/management/persona-league`. Do not conflate, alias, or merge these surfaces.

### D2 — `human-inbox`: Canonical Route Segment

**Decision:** `human-inbox` is the canonical URL segment for the Management Human Inbox aggregate
and detail routes. There is no competing form.

Routes:
- `GET /bff/management/human-inbox` (BFF-B3-003 aggregate)
- `GET /bff/management/human-inbox/{id}` (BFF-B3-004 detail)

**Guidance:**

| Form | Status | Rule |
|---|---|---|
| `human-inbox` | **KEEP** | Canonical kebab-case segment. |
| `humanInbox` | N/A | Not a URL segment; TypeScript builder name only. |
| `human_inbox` | **DEPRECATE in URLs** | Must not appear in new URL path segments. |

The `human_inbox` snake_case form appears in Python response field names (e.g., `inbox_type`,
`source_record`) and is governed by the dual-emit policy in Section 2, not this section.

### D3 — `trading-pulse`: Canonical Route Segment

**Decision:** `trading-pulse` is the canonical URL segment for the Management Trading Pulse
telemetry routes. There is no competing form.

Routes:
- `GET /bff/management/trading-pulse` (BFF-B3-004 cards)
- `GET /bff/management/trading-pulse/rankings` (BFF-B3-005 ranking blocks)

**Guidance:**

| Form | Status | Rule |
|---|---|---|
| `trading-pulse` | **KEEP** | Canonical kebab-case segment. |
| `tradingPulse` | N/A | TypeScript builder name only; not a URL segment. |
| `trading_pulse` | **DEPRECATE in URLs** | Must not appear in new URL path segments. |

### D4 — `evolution-journal`: Canonical Route Segment

**Decision:** `evolution-journal` is the canonical URL segment for the Management Evolution
Journal aggregate route. There is no competing form.

Route:
- `GET /bff/management/evolution-journal` (BFF-B3-006)

Query filter parameters: `decision`, `mutation_review`, `postmortem`, `freeze_order`, `rollback`
(all snake_case, per Section 1 rule A3/A4).

**Guidance:**

| Form | Status | Rule |
|---|---|---|
| `evolution-journal` | **KEEP** | Canonical kebab-case segment. |
| `evolutionJournal` | N/A | TypeScript builder name only; not a URL segment. |
| `evolution_journal` | **DEPRECATE in URLs** | Must not appear in new URL path segments. |

### D5 — `evidence`: Canonical Route Segment

**Decision:** `evidence` is the canonical URL segment for the Management Evidence Explorer
aggregate route. It is a top-level management route, not a sub-path under another aggregate.

Route:
- `GET /bff/management/evidence` (BFF-B3-007)

**Guidance:**

| Form | Status | Rule |
|---|---|---|
| `evidence` | **KEEP** | Canonical single-word segment. Correct. |
| `management-evidence` | **DEPRECATE** | Not used; do not introduce. |
| `evidence-explorer` | **DEPRECATE** | Descriptive label only; not a URL segment. |

The response field `management_href` / `managementHref` (SNAKE-DUP-012 in Section 2) is governed
by the dual-emit policy, not by this route-segment decision.

### D6 — `portfolio-book` Sub-Routes: Naming and Status

**Decision:** The four `portfolio-book` sub-routes below are all canonical, active, and must
coexist. They are not aliases of each other.

| Sub-route | Full URL | Task origin | Scope | Status |
|---|---|---|---|---|
| `holdings` | `/bff/management/portfolio-book/holdings` | BFF-PM12-002 | Global holdings table (positions/fills/mark prices) | **KEEP** |
| `pools` | `/bff/management/portfolio-book/pools` | BFF-PM12-003 | Capital pool list + exposure + risk budget + PnL | **KEEP** |
| `positions` | `/bff/management/portfolio-book/positions` | DELTA-5 (PM12-DELTA-005) | Position rows projected from holdings; added in v3 delta | **KEEP** |
| `exposure` | `/bff/management/portfolio-book/exposure` | DELTA-6 (PM12-DELTA-006) | Risk-budget/exposure view projected from pools; added in v3 delta | **KEEP** |

**Key disambiguation:**

- `positions` is not an alias for `holdings`. `positions` projects the holdings data as
  position-oriented rows. The underlying composition source is the same, but the route and response
  shape are distinct. Callers requesting position-level rows use `/portfolio-book/positions`;
  callers requesting raw holdings use `/portfolio-book/holdings`.
- `exposure` is not an alias for `pools`. `exposure` projects the pool data as a risk-budget /
  current-exposure view. The underlying source is the same, but the response shape is distinct.
  Callers requesting exposure rows use `/portfolio-book/exposure`; callers requesting pool
  summaries use `/portfolio-book/pools`.

**Guidance for all four sub-routes:**

All four sub-routes use kebab-case (per Section 1 rule A1). The base path `/portfolio-book` and
all sub-route segments (`holdings`, `pools`, `positions`, `exposure`) are canonical and must not
be introduced in snake_case or camelCase form in URL paths.

---

## Section 5 — Keep / Deprecate / Both Reference Table

This table is the single-page quick reference for keep/deprecate/both decisions across all naming
dimensions in this document. Implementors should check this table before introducing a new route
segment, query parameter form, or response field form.

### URL Path Segments

| Segment | Status | Notes |
|---|---|---|
| `persona-fleet` | **KEEP** | Canonical. Distinct from persona-league. |
| `persona-league` | **KEEP** | Canonical. Distinct from persona-fleet. |
| `human-inbox` | **KEEP** | Canonical. |
| `trading-pulse` | **KEEP** | Canonical. |
| `evolution-journal` | **KEEP** | Canonical. |
| `evidence` | **KEEP** | Canonical. Top-level management aggregate. |
| `portfolio-book` | **KEEP** | Canonical base. |
| `portfolio-book/holdings` | **KEEP** | Canonical. Holdings table. |
| `portfolio-book/pools` | **KEEP** | Canonical. Capital pool summaries. |
| `portfolio-book/positions` | **KEEP** | Canonical. v3 delta addition. |
| `portfolio-book/exposure` | **KEEP** | Canonical. v3 delta addition. |
| `*_*` in path segments | **DEPRECATE** | snake_case must not appear in URL path segments. |
| `*[A-Z]*` in path segments | **DEPRECATE** | camelCase must not appear in URL path segments. |

### Query Parameters

| Form | Status | Notes |
|---|---|---|
| snake_case (`persona_id`, `window_hours`) | **KEEP — canonical** | Use as the primary key. |
| camelCase alias (`personaId`, `windowHours`) | **BOTH — alias only** | Accept only when a confirmed FE caller sends it; add explicit `or`-coalesce in handler. |

### Response Fields (body)

| Form | Status | Notes |
|---|---|---|
| snake_case field (canonical) | **KEEP** | Primary authoritative form. |
| camelCase alias (dual-emit) | **BOTH — alias only** | Emit alongside snake_case when the field is in Section 2's dual-field table. |
| camelCase on a field not in Section 2 | **DEPRECATE** | Do not add new dual-emit fields without explicit review approval citing this document. |

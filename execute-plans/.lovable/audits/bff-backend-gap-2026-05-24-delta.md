# execute-plans BFF Backend Gap Delta - 2026-05-24

Status: task-scoped audit record

## BFF-MGMT-DELTA-001

Route:

```text
GET /bff/management/persona-league/movers
```

Purpose:

Expose a live BFF Management endpoint for persona-league movement cards/lists
without requiring execute-plans to fan out through raw persona, ranking, tier,
and health routes.

Frontend contract:

- `paths.managementPersonaLeagueMovers()`
- `managementPersonaLeagueMoversPath(query)`
- `fetchManagementPersonaLeagueMovers(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- invalid `direction`: HTTP 422
- response envelope: `data`, `items`, `movers`, `summary`, `page_info`, `meta`
- `policy`: `read_only_governance_advisory`
- `baselineStatus`: `unavailable`
- `direction`: `new` until historical persona-league snapshots exist
- `meta.surfaces.persona_league_history`: degraded

Validation:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

Reviewer result before closeout merge with `origin/dev`: 18 passed, 3 existing
`datetime.utcnow()` deprecation warnings in `services/control-plane/bff/read_store.py`.

Closeout merge validation with `origin/dev`:

```bash
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 62 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-010

Route:

```text
GET /bff/management/loop-throughput
```

Purpose:

Provide a strict-live Management Console route for loop execution throughput.
The backend route is a read-only aggregate over the v5 loop-run surface used by
`/bff/v5/loop-runs`; it exposes loop runs per minute, queue depth, queue lag,
status buckets, runtime refs, and loop detail links without introducing a new
loop source of truth.

Frontend contract:

- `paths.managementLoopThroughput()`
- `managementLoopThroughputPath(query)`
- `fetchManagementLoopThroughput(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `loops`, `summary`, `metrics`,
  `page_info`, `meta`
- supported query: `status`, `runtime_id`, `page_token`, `page_size`
- `data.id`: `management-loop-throughput`
- `summary.runs_per_minute`, `summary.queue_depth`, and
  `summary.max_queue_lag_seconds` are populated from v5 loop-run timestamps
- `meta.policy`: `read_only_loop_throughput`
- no new loop source of truth; composed from v5 loop runs and incident-derived
  loop runs where applicable

Validation:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: `git diff --check` exited 0; broader BFF delta suite passed with 86
tests and 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-011

Route:

```text
GET /bff/management/hiq-backlog
```

Purpose:

Provide a strict-live Management Console route for the HIQ operator backlog.
The backend route is a read-only aggregate over existing v5 intervention,
sentinel finding, and human-inbox read surfaces; it does not introduce a new
backlog source of truth and does not mutate interventions, findings, approvals,
or capital.

Frontend contract:

- `paths.managementHiqBacklog()`
- `managementHiqBacklogPath(query)`
- `fetchManagementHiqBacklog(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204/200
- response envelope: `data`, `items`, `rows`, `backlog`, `summary`,
  `page_info`, `meta`
- supported query: `source_type`, `status`, `kind`, `priority`, `q`,
  `page_token`, `page_size`
- default backlog filter: `kind=hiq_sentinel,risk_breach` and open/pending
  backlog statuses unless caller passes `kind=all` or `status=all`
- `data.id`: `management-hiq-backlog`
- `meta.policy`: `read_only_hiq_backlog`
- no new HIQ source of truth; composed from v5 interventions, sentinel
  findings, and human-inbox surfaces

Validation:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: `git diff --check` exited 0; focused route/live-wiring suite passed
with 22 tests; broader BFF delta suite passed with 84 tests and 3 existing
`datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-006

Route:

```text
GET /bff/management/incident-timeline
```

Purpose:

Provide a strict-live Management Console route for incident timeline rendering.
The backend route is a read-only aggregate over the existing IncidentCase read
surface used by `/bff/incidents`; it preserves incident evidence fields and
adds chronological timeline fields plus severity buckets for high, medium, and
low incidents.

Frontend contract:

- `paths.managementIncidentTimeline()`
- `managementIncidentTimelinePath(query)`
- `fetchManagementIncidentTimeline(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `incidents`, `events`,
  `summary`, `severityBuckets`, `page_info`, `meta`
- `data.id`: `management-incident-timeline`
- `meta.policy`: `read_only_incident_timeline`
- rows are sorted by occurrence time and include `severityBucket` /
  `severity_bucket`
- `summary.severityBuckets` exposes `high`, `medium`, and `low`
- no new incident source of truth; composed from IncidentCase read surfaces

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 75 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-007

Route:

```text
GET /bff/management/governance-ledger
```

Purpose:

Provide a strict-live Management Console route for a unified governance ledger.
The backend route composes approval queue/decision records, v5 interventions,
and governance audit events that represent approvals, interventions, and
overrides. It is read-only and does not introduce a new governance source of
truth.

Frontend contract:

- `paths.managementGovernanceLedger()`
- `managementGovernanceLedgerPath(query)`
- `fetchManagementGovernanceLedger(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `entries`, `ledger`, `summary`, `page_info`, `meta`
- supported query: `source_type`, `status`, `q`, `page_token`, `page_size`
- `data.id`: `management-governance-ledger`
- `meta.policy`: `read_only_governance_ledger`
- no new governance source of truth; composed from audit, approvals, and v5
  intervention read surfaces
- no new sentinel source of truth; composed from `/bff/v5/sentinel/findings`
  and `/bff/v5/interventions`

Validation:

```bash
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 81 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-009

Route:

```text
GET /bff/management/sentinel-pulse
```

Purpose:

Provide a strict-live Management Console route for the sentinel first-screen
pulse. The backend route composes existing v5 sentinel findings and v5
interventions into a read-only Management aggregate without introducing a new
sentinel source of truth or write path.

Frontend contract:

- `paths.managementSentinelPulse()`
- `managementSentinelPulsePath(query)`
- `fetchManagementSentinelPulse(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `findings`, `interventions`, `cards`, `summary`, `page_info`, `meta`
- supported query: `kind`, `status`, `severity`, `q`, `page_token`, `page_size`
- `data.id`: `management-sentinel-pulse`
- `meta.policy`: `read_only_sentinel_pulse`
- no new sentinel source of truth; composed from `/bff/v5/sentinel/findings`
  and `/bff/v5/interventions`

Validation:

```bash
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

Result after rebase with `origin/dev`: 21 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-PM12-DELTA-002

Route:

```text
GET /bff/management/performance-attribution/by-persona
```

Purpose:

Provide a dedicated strict-live Management Console route for PM-12 performance
attribution grouped by persona. The backend route is a narrow wrapper around
the existing PM-12 attribution composer with `dimensions=["persona"]`.

Frontend contract:

- `paths.managementPerformanceAttributionByPersona()`
- `managementPerformanceAttributionByPersonaPath(query)`
- `fetchManagementPerformanceAttributionByPersona(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `summary`, `page_info`, `meta`
- `summary.dimensions`: `["persona"]`
- row `dimension`: `persona`
- `meta.policy`: `read_only_performance_attribution`

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 43 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-003

Route:

```text
GET /bff/management/strategy-allocation
```

Purpose:

Provide a strict-live Management Console route for active strategy allocation
across capital pools, including paper/live drift status for the active runtimes
that back each strategy/pool allocation row.

Frontend contract:

- `paths.managementStrategyAllocation()`
- `managementStrategyAllocationPath(query)`
- `fetchManagementStrategyAllocation(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `summary`, `page_info`, `meta`
- `data.id`: `management-strategy-allocation`
- row contains `strategy_id`, `capital_pool_id`, allocation fields, runtime refs, and `drift`
- `meta.policy`: `read_only_strategy_allocation`

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 49 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-004

Route:

```text
GET /bff/management/capital-flow
```

Purpose:

Provide a strict-live Management Console route for read-only capital flow
projections across capital pools, personas, strategies, and runtime deployment
stages. The backend composes existing runtime, deployment, binding, pool,
strategy, and telemetry surfaces; it does not introduce a new capital ledger or
mutate capital.

Frontend contract:

- `paths.managementCapitalFlow()`
- `managementCapitalFlowPath(query)`
- `fetchManagementCapitalFlow(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `flows`, `summary`, `page_info`, `meta`
- supported query: `capital_pool_id`, `persona_id`, `strategy_id`,
  `deployment_stage`, `direction`, `page_token`, `page_size`
- `data.id`: `management-capital-flow`
- `meta.policy`: `read_only_capital_flow`
- no new capital source of truth; composed from runtime bindings, deployment
  plans, persona bindings, capital pools, strategies, and telemetry summaries

Validation:

```bash
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 79 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-PM12-DELTA-004

Route:

```text
GET /bff/management/performance-attribution/by-pool
```

Purpose:

Provide a dedicated strict-live Management Console route for PM-12 performance
attribution grouped by capital pool. The backend route is a narrow wrapper
around the existing PM-12 attribution composer with `dimensions=["pool"]`.

Frontend contract:

- `paths.managementPerformanceAttributionByPool()`
- `managementPerformanceAttributionByPoolPath(query)`
- `fetchManagementPerformanceAttributionByPool(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `summary`, `page_info`, `meta`
- `summary.dimensions`: `["pool"]`
- row `dimension`: `pool`
- `meta.policy`: `read_only_performance_attribution`

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 46 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-PM12-DELTA-005

Route:

```text
GET /bff/management/portfolio-book/positions
```

Purpose:

Provide a dedicated strict-live Management Console route for PM-12 global
portfolio positions. The backend route is a read-only projection over the
existing PM-12 portfolio-book holdings composer, preserving runtime,
capital-pool, persona, strategy, symbol, quantity, mark, market value, PnL,
links, and source refs while adding `position_id` / `positionId` aliases for
frontend table identity.

Frontend contract:

- `paths.managementPortfolioBookPositions()`
- `managementPortfolioBookPositionsPath(query)`
- `fetchManagementPortfolioBookPositions(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `positions`, `summary`, `page_info`, `meta`
- supported query: `capital_pool_id`, `persona_id`, `runtime_id`,
  `deployment_stage`, `status`, `q`, `page_token`, `page_size`
- `meta.surfaces.portfolio_book_positions`: `bff_composed`
- no new positions source of truth; composed from runtime bindings,
  deployment plans, persona bindings, capital pools, and telemetry summaries

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 58 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-PM12-DELTA-006

Route:

```text
GET /bff/management/portfolio-book/exposure
```

Purpose:

Provide a dedicated strict-live Management Console route for PM-12
portfolio-book exposure. The backend route is a narrow read-only aggregate
around the existing PM-12 portfolio-book pool composer and surfaces risk
budget, current exposure, utilization, risk state, source refs, and capital
pool drilldown links.

Frontend contract:

- `paths.managementPortfolioBookExposure()`
- `managementPortfolioBookExposurePath(query)`
- `fetchManagementPortfolioBookExposure(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `exposures`, `summary`, `page_info`, `meta`
- `data.id`: `pm12-portfolio-book-exposure`
- `meta.policy`: `read_only_portfolio_exposure`
- no new exposure source of truth; composed from capital pools, persona
  bindings, deployment plans, runtime bindings, and telemetry summaries

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 53 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-PM12-DELTA-007

Route:

```text
GET /bff/management/board-pack
```

Purpose:

Provide a dedicated strict-live Management Console board packet for PM-12. The
backend route composes existing read-only Management surfaces into a single
board-level payload for first-screen rendering without creating a new source of
truth.

Frontend contract:

- `paths.managementBoardPack()`
- `managementBoardPackPath(query)`
- `fetchManagementBoardPack(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `sections`, `summary`, `page_info`, `meta`
- `data.id`: `management-board-pack`
- supported query: `period`, `state`, `archetype`, `q`, `section_limit`
- `meta.policy`: `read_only_management_board_pack`
- no new PM-12 source of truth; composed from portfolio-book, exposure,
  positions, strategy allocation, persona league, movers, and attribution routes

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 66 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-008

Route:

```text
GET /bff/management/cost-attribution
```

Purpose:

Provide a strict-live Management Console route for read-only cost attribution
across personas, strategies, and capital pools. The backend route composes
existing runtime, deployment, binding, pool, strategy, and telemetry surfaces
to derive commission cost, slippage cost, and infrastructure cost for each
persona/strategy/pool combination. It does not introduce a new cost ledger or
mutate capital.

Frontend contract:

- `paths.managementCostAttribution()`
- `managementCostAttributionPath(query)`
- `fetchManagementCostAttribution(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `attributions`, `summary`, `page_info`, `meta`
- supported query: `persona_id`, `strategy_id`, `capital_pool_id`, `period`,
  `page_token`, `page_size`
- `data.id`: `management-cost-attribution`
- `meta.policy`: `read_only_cost_attribution`
- rows include `cost_id`, identifiers, `total_cost`, `commission_cost`,
  `slippage_cost`, `infrastructure_cost`, `allocated_capital`, and source refs
- no new cost source of truth; composed from runtime bindings, deployment
  plans, persona bindings, capital pools, strategies, and telemetry summaries

Validation:

```bash
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 84 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## BFF-MGMT-DELTA-005

Route:

```text
GET /bff/management/risk-radar
```

Purpose:

Provide a strict-live Management Console route for cross-persona and strategy
risk indicators. The backend route is a read-only aggregate over runtime
bindings, deployment plans, persona-capital bindings, capital pools, strategy
summaries, and telemetry summaries. It surfaces drawdown, exposure,
value-at-risk, risk state, runtime source refs, and detail links without
introducing a new risk source of truth.

Frontend contract:

- `paths.managementRiskRadar()`
- `managementRiskRadarPath(query)`
- `fetchManagementRiskRadar(query, init, baseUrl)`

Backend acceptance:

- unauthenticated request: HTTP 401
- authenticated request: HTTP 200
- CORS preflight: HTTP 204
- response envelope: `data`, `items`, `rows`, `indicators`, `summary`, `page_info`, `meta`
- `data.id`: `management-risk-radar`
- `meta.policy`: `read_only_risk_radar`
- rows include persona, strategy, capital pool, drawdown, exposure,
  value-at-risk, metric indicator statuses, and source refs
- no new risk source of truth; composed from runtime bindings, deployment
  plans, persona bindings, capital pools, strategies, and telemetry summaries

Validation:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 62 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

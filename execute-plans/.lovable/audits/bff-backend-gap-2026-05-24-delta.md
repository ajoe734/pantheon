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

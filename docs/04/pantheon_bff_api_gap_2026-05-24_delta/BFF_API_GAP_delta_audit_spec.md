# BFF API GAP - Delta Audit Spec

Status: active
Date: 2026-05-24
Sprint: Sprint BFF-DELTA

This document records BFF deltas found after the 2026-05-23 final integration
spec. It is an execution record, not a new L1 product authority.

---

## DELTA-1 CORS Preflight Regression - execute-plans Origin Blocked in Live Mode

Task: BFF-B1-001-DELTA
Owner: Claude
Reviewer: Codex

### Gap

After BFF-B1-001 added `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`
to `_DEFAULT_LOVABLE_CORS_ORIGINS`, it was simultaneously added to
`_DEV_LOVABLE_CORS_ORIGINS`.

`_DEV_LOVABLE_CORS_ORIGINS` is the set of origins stripped from the allowlist
when the BFF runs in production strict mode (`PANTHEON_ENV=production/live/canary`
and `PANTHEON_BFF_AUTH_MODE=strict`). The production strict filter in
`_cors_origins_from_env()` removes any origin found in `_DEV_LOVABLE_CORS_ORIGINS`.

As a result, when the live BFF served an OPTIONS preflight from the execute-plans
frontend (`https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`), no
matching `Access-Control-Allow-Origin` header was returned, and Starlette's
CORSMiddleware responded with HTTP 400. All subsequent CORS-gated requests from
the live execute-plans frontend therefore failed.

The preview-URL regex (`_LOVABLE_PREVIEW_ORIGIN_REGEX`) is disabled in strict
mode (`preview_regex = None`), so regex fallback could not compensate.

### Root Cause

`https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` is the
published URL for the execute-plans Lovable project, not a dev/preview URL. It
was incorrectly classified as dev-only when added by BFF-B1-001.

### Fix

**File: `services/control-plane/bff/main.py`**

Remove `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` from
`_DEV_LOVABLE_CORS_ORIGINS`. The URL remains in `_DEFAULT_LOVABLE_CORS_ORIGINS`
and now survives the production strict filter unchanged.

BFF-PM12-DELTA-002 also standardizes successful CORS preflight responses to
HTTP 204 through the BFF CORS middleware. Current regression tests therefore
assert successful preflight as HTTP 204 with the matching
`Access-Control-Allow-Origin` header.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `_cors_origins_from_env()` includes `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` when `PANTHEON_ENV=production` and `PANTHEON_BFF_AUTH_MODE=strict` | Fixed |
| 2 | OPTIONS preflight from `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` succeeds with HTTP 204 and matching `Access-Control-Allow-Origin` in strict/production mode | Fixed |
| 3 | Dev-only origins (`pantheon-dev.lovable.app`, `b75d3452-...-lovableproject.com`) are still filtered in strict mode | Unchanged |
| 4 | Dynamic preview URLs (`id-preview-<hash>--<uuid>.lovable.app`) are still blocked in strict mode (regex disabled) | Unchanged |
| 5 | `pytest -q services/control-plane/bff/tests/test_auth_jwks_strict.py` exits 0 | Verified |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

---

## BFF-MGMT-DELTA-001 Persona League Movers Route

Task: BFF-MGMT-DELTA-001
Owner: Codex
Reviewer: Claude

### Scope

Add a dedicated Management route for execute-plans strict live rendering:

```text
GET /bff/management/persona-league/movers?state=&archetype=&q=&direction=&limit=
```

The route exposes persona-league movement cards/lists without requiring
execute-plans to fan out through raw persona, ranking, tier, and health routes.
It is read-only and uses `policy=read_only_governance_advisory`.

### Contract

The response uses the canonical Management list envelope:

```json
{
  "data": {
    "id": "management-persona-league-movers",
    "items": [],
    "movers": [],
    "summary": {},
    "policy": "read_only_governance_advisory"
  },
  "items": [],
  "movers": [],
  "summary": {},
  "page_info": {
    "next_page_token": null,
    "total": 0,
    "page_size": 0
  },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": [],
    "policy": "read_only_governance_advisory"
  }
}
```

Each mover item includes persona identifiers, current rank and score, previous
rank and score placeholders, rank and score deltas, direction, tier, metrics,
score components, links, formula version, and movement basis.

Historical persona-league snapshots are not yet a first-class read source in
the BFF. Until that source exists, returned items use
`baselineStatus=unavailable`, `direction=new`, null delta fields, and
`basis=current_persona_league_snapshot_no_historical_baseline`.

### Composition Sources

- `GET /bff/management/persona-league`
- `GET /bff/management/persona-league/rankings`
- `GET /bff/management/persona-league/tiers`
- `GET /bff/personas`
- `GET /bff/v5/execution/persona-health`

`meta.surfaces` includes `persona_league_movers`, `persona_league_history`, and
the PM-12 persona-league source surfaces. The history surface is degraded while
historical baseline data is unavailable.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Authenticated GET returns `data`, `items`, `movers`, `summary`, `page_info`, and `meta` | Implemented |
| 2 | Route supports `state`, `archetype`, `q`, `direction`, and `limit` | Implemented |
| 3 | Invalid `direction` returns HTTP 422 | Implemented |
| 4 | Missing auth returns HTTP 401 | Implemented |
| 5 | execute-plans exposes path, query/response types, and fetch helper | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_bff_pm12_persona_league.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

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

---

## DELTA-2 PM-12 Persona Performance Attribution Route

Task: BFF-PM12-DELTA-002
Owner: Codex2
Reviewer: Claude2

### Scope

Add a dedicated persona-grouped attribution route for execute-plans strict live
rendering:

```text
GET /bff/management/performance-attribution/by-persona?period=&page_token=&page_size=
```

The route reuses the PM-12 performance attribution composition logic and fixes
the attribution dimension to `persona`. It does not introduce a new performance
source of truth and does not change the generic
`GET /bff/management/performance-attribution` route.

### Contract

The response uses the canonical aggregate envelope:

```json
{
  "data": {
    "id": "pm12-performance-attribution-by-persona",
    "period": "latest",
    "dimensions": ["persona"],
    "items": [],
    "rows": [],
    "summary": {}
  },
  "items": [],
  "rows": [],
  "summary": {},
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50 },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": [],
    "policy": "read_only_performance_attribution"
  }
}
```

Rows keep the existing attribution row schema: `dimension`,
`dimensionKey` / `dimension_key`, `label`, `rank`, `period`, nested `metrics`,
top-level contribution fields, `sourceRefs` / `source_refs`, and persona
drilldown links when available.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Implemented |
| 2 | Attribution grouped by persona dimension | Implemented |
| 3 | Accepts `period`, `page_token`, and `page_size` query parameters | Implemented |
| 4 | Anonymous request returns HTTP 401 | Implemented |
| 5 | Authenticated request returns HTTP 200 | Implemented |
| 6 | Response keeps canonical aggregate envelope | Implemented |
| 7 | CORS preflight returns HTTP 204 | Implemented |
| 8 | Focused pytest case covers `attribution_by_persona` | Implemented |
| 9 | execute-plans exposes typed path and fetch helpers | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 43 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

---

## DELTA-3 Management Strategy Allocation Route

Task: BFF-MGMT-DELTA-003
Owner: Codex
Reviewer: Claude

### Scope

Add a strict-live Management Console route for active strategy allocation across
capital pools:

```text
GET /bff/management/strategy-allocation?strategy_id=&capital_pool_id=&deployment_stage=&drift_status=&page_token=&page_size=
```

The route composes runtime bindings, deployment plans, persona-capital bindings,
capital pools, strategy summaries, telemetry snapshots, and paper/live drift
reports. It is read-only and does not introduce a new allocation source of
truth or mutate capital.

### Contract

The response uses the canonical aggregate envelope:

```json
{
  "data": {
    "id": "management-strategy-allocation",
    "items": [],
    "rows": [],
    "summary": {}
  },
  "items": [],
  "rows": [],
  "summary": {},
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50 },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": [],
    "policy": "read_only_strategy_allocation"
  }
}
```

Rows include `strategyId` / `strategy_id`, `capitalPoolId` /
`capital_pool_id`, allocation amount and risk-budget utilization, source refs,
runtime/deployment/persona references, links, metrics, and a paper/live drift
summary with per-runtime drift report refs.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Implemented |
| 2 | Active strategy allocation slice across capital pools | Implemented |
| 3 | Includes paper/live drift status and metric counts | Implemented |
| 4 | Anonymous request returns HTTP 401 | Implemented |
| 5 | Authenticated request returns HTTP 200 | Implemented |
| 6 | Response keeps canonical aggregate envelope | Implemented |
| 7 | CORS preflight returns HTTP 204 | Implemented |
| 8 | Focused pytest case covers `strategy_allocation` | Implemented |
| 9 | execute-plans exposes typed path and fetch helpers | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 49 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

---

## DELTA-4 PM-12 Capital Pool Performance Attribution Route

Task: BFF-PM12-DELTA-004
Owner: Codex2
Reviewer: Claude2

### Scope

Add a dedicated capital-pool grouped attribution route for execute-plans strict
live rendering:

```text
GET /bff/management/performance-attribution/by-pool?period=&page_token=&page_size=
```

The route reuses the PM-12 performance attribution composition logic and fixes
the attribution dimension to `pool`. It does not introduce a new performance
source of truth and does not change the generic
`GET /bff/management/performance-attribution` route.

### Contract

The response uses the canonical aggregate envelope:

```json
{
  "data": {
    "id": "pm12-performance-attribution-by-pool",
    "period": "latest",
    "dimensions": ["pool"],
    "items": [],
    "rows": [],
    "summary": {}
  },
  "items": [],
  "rows": [],
  "summary": {},
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50 },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": [],
    "policy": "read_only_performance_attribution"
  }
}

```

Rows keep the existing attribution row schema: `dimension`,
`dimensionKey` / `dimension_key`, `label`, `rank`, `period`, nested `metrics`,
top-level contribution fields, `sourceRefs` / `source_refs`, and capital-pool
drilldown links when available.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Implemented |
| 2 | Attribution grouped by capital pool dimension | Implemented |
| 3 | Accepts `period`, `page_token`, and `page_size` query parameters | Implemented |
| 4 | Anonymous request returns HTTP 401 | Implemented |
| 5 | Authenticated request returns HTTP 200 | Implemented |
| 6 | Response keeps canonical aggregate envelope | Implemented |
| 7 | CORS preflight returns HTTP 204 | Implemented |
| 8 | Focused pytest case covers `attribution_by_pool` | Implemented |
| 9 | execute-plans exposes typed path and fetch helpers | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 46 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

---

## DELTA-5 PM-12 Portfolio Book Positions Route

Task: BFF-PM12-DELTA-005
Owner: Codex2
Reviewer: Claude2

### Scope

Add a dedicated portfolio-book positions route for execute-plans strict live
rendering:

```text
GET /bff/management/portfolio-book/positions?capital_pool_id=&persona_id=&runtime_id=&deployment_stage=&status=&q=&page_token=&page_size=
```

The route reuses the existing PM-12 portfolio-book holdings composition and
projects holdings as position rows. It does not introduce a new positions
source of truth and does not change
`GET /bff/management/portfolio-book/holdings`.

### Contract

The response uses the canonical aggregate envelope:

```json
{
  "data": {
    "summary": {},
    "items": [],
    "positions": []
  },
  "items": [],
  "positions": [],
  "summary": {},
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50 },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": []
  }
}
```

Position rows keep the existing holding fields, including runtime, capital
pool, persona, strategy, symbol, quantity, mark price, market value, PnL, links,
and source refs. Each row additionally exposes `position_id` and `positionId`
derived from the composed holding identity.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Implemented |
| 2 | Positions list composes from portfolio-book holdings sources | Implemented |
| 3 | Accepts pool/persona/runtime/stage/status/search/page query parameters | Implemented |
| 4 | Anonymous request returns HTTP 401 | Implemented |
| 5 | Authenticated request returns HTTP 200 | Implemented |
| 6 | Response keeps canonical aggregate envelope | Implemented |
| 7 | CORS preflight returns HTTP 204 | Implemented |
| 8 | Focused pytest cases cover list, filter, auth, preflight, and degraded telemetry | Implemented |
| 9 | execute-plans exposes typed path and fetch helpers | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 58 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

---

## DELTA-6 PM-12 Portfolio Book Exposure Route

Task: BFF-PM12-DELTA-006
Owner: Codex2
Reviewer: Claude2

### Scope

Add a dedicated portfolio-book exposure route for execute-plans strict live
rendering:

```text
GET /bff/management/portfolio-book/exposure?status=&risk_policy_ref=&capital_pool_id=&page_token=&page_size=
```

The route reuses the existing PM-12 portfolio-book pool composition and exposes
the risk-budget / current-exposure view as a first-class read-only aggregate.
It does not introduce a new exposure source of truth and does not change
`GET /bff/management/portfolio-book/pools`.

### Contract

The response uses the canonical aggregate envelope:

```json
{
  "data": {
    "id": "pm12-portfolio-book-exposure",
    "summary": {},
    "items": [],
    "exposures": []
  },
  "items": [],
  "exposures": [],
  "summary": {},
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50 },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": [],
    "policy": "read_only_portfolio_exposure"
  }
}
```

Exposure rows keep the portfolio-book pool identifiers and include
`risk_budget` / `riskBudget`, `current_exposure` / `currentExposure`,
`risk_budget_utilization` / `riskBudgetUtilization`, `risk_state` /
`riskState`, `available_budget` / `availableBudget`, source refs, and capital
pool drilldown links when available.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Implemented |
| 2 | Exposure rows compose from portfolio-book pool sources | Implemented |
| 3 | Accepts `status`, `risk_policy_ref`, `capital_pool_id`, `page_token`, and `page_size` query parameters | Implemented |
| 4 | Anonymous request returns HTTP 401 | Implemented |
| 5 | Authenticated request returns HTTP 200 | Implemented |
| 6 | Response keeps canonical aggregate envelope | Implemented |
| 7 | CORS preflight returns HTTP 204 | Implemented |
| 8 | Focused pytest cases cover exposure rollup, filter, auth, and preflight | Implemented |
| 9 | execute-plans exposes typed path and fetch helpers | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 53 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

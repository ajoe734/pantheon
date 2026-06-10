# Review: BFF-PM12-003 - GET /bff/management/portfolio-book/pools

Reviewer: Claude2
Reviewed at: 2026-05-23
Status: **approved**

## Scope

Task: `GET /bff/management/portfolio-book/pools` - capital pool summaries (B3-016).

Artifact set reviewed:
- `services/control-plane/bff/main.py` - route + composition helpers
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py` - contract tests
- `execute-plans/src/lib/bff-v1/management.ts` - FE type contracts + fetch helper
- `execute-plans/src/lib/bff-v1/paths.ts` - path builder

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py  -> OK
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -v
  11 passed in 4.70s OK (includes 3 new pool-specific tests)
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py -v
  5 passed in 3.56s OK (no regression)
```

## Review Findings

### Route Shape - Pass

`/bff/management/portfolio-book/pools` returns `data`, `items`, and `pools` list
aliases (needed for strict-fallback UI), `summary`, `page_info` (including
`page_size`), and `meta` with per-surface status and `composition_sources`. The
shape matches the B3.2 / B3.4 spec requirement and aligns with the portfolio-book
summary route convention established by BFF-PM12-001.

### Composition Logic - Pass

`_management_portfolio_book_pool_sources()` correctly:
- Fetches capital pools with pass-through `status` / `risk_policy_ref` filters.
- Joins bindings, deployment plans, and runtime bindings by pool id or binding-id
  intersection.
- Collects telemetry per runtime; feeds `_management_telemetry_rollup()` for PnL,
  drawdown, fill-rate, and trade count aggregates.

`_management_portfolio_book_entry()` correctly:
- Projects `risk_budget_utilization` (current_exposure / risk_budget).
- Exposes camelCase aliases (`riskBudget`, `currentExposure`,
  `riskBudgetUtilization`) in addition to snake_case for execute-plans DTO
  compatibility.
- Reports per-pool binding / deployment / runtime counts and stage breakdowns.

### Degraded-Surface Handling - Pass

When `get_telemetry_summary` returns nothing, the `telemetry_summaries` surface is
marked `unavailable` and `portfolio_book_pools` is marked `degraded`. Core pool
data (capital pool list, exposure, risk budget) remains visible. This satisfies the
VITE_BFF_FALLBACK=strict requirement.

### Auth Gate - Pass

Route calls `_require_read_role(identity)` -> HTTP 401 for unauthenticated
requests. Verified by `test_portfolio_book_pools_requires_read_auth`.

### Pagination And Filtering - Pass

`page_token` / `page_size` delegates to `_page_slice()`. `status` and
`risk_policy_ref` are forwarded to `read_store.list_capital_pools()`. Verified by
`test_portfolio_book_pools_filters_and_paginates`.

### FE Contracts - Pass

`ManagementPortfolioBookPoolItem` and `ManagementPortfolioBookPoolsResponse` types
cover all fields emitted by the backend. `managementPortfolioBookPoolsPath()` and
`fetchManagementPortfolioBookPools()` are consistent with the patterns used by the
other PM-12 fetch helpers.

### OpenAPI Registration - Pass

`test_portfolio_book_is_registered_in_openapi` confirms the route appears in the
generated schema.

## Approval

Implementation is complete and satisfies the B3-016 spec requirement. All
acceptance criteria derivable from the spec and test suite are met. No issues
requiring changes.

Returned to Codex2 for closeout finalization.

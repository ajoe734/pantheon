# Review: BFF-PM12-DELTA-006

Reviewer: Claude2
Date: 2026-05-24
Status: approved

## Summary

Reviewed implementation of `GET /bff/management/portfolio-book/exposure`
added by Codex2. PR #523 merged to dev at 9b6e9970.

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Pass — `@app.get("/bff/management/portfolio-book/exposure")` at main.py:22494; included in live wiring contract test |
| 2 | Exposure rows compose from portfolio-book pool sources | Pass — `_management_portfolio_book_pool_sources()` called; exposes risk-budget / current-exposure rollup via `_management_portfolio_book_exposure_item()` |
| 3 | Accepts `status`, `risk_policy_ref`, `capital_pool_id`, `page_token`, `page_size` | Pass — all five query parameters declared in handler signature |
| 4 | Anonymous request returns HTTP 401 | Pass — `test_portfolio_book_exposure_requires_read_auth` |
| 5 | Authenticated request returns HTTP 200 | Pass — `test_portfolio_book_exposure_composes_risk_budget_rollup` validates full response shape |
| 6 | Response keeps canonical aggregate envelope | Pass — `data`, `items`, `exposures`, `summary`, `page_info`, `meta` all present; `data.id` is `pm12-portfolio-book-exposure`; `meta.policy` is `read_only_portfolio_exposure` |
| 7 | CORS preflight returns HTTP 204 | Pass — `test_portfolio_book_exposure_cors_preflight` |
| 8 | Focused pytest cases cover exposure rollup, filter, auth, and preflight | Pass — four focused test cases covering all scenarios |
| 9 | execute-plans exposes typed path and fetch helpers | Pass — `ManagementPortfolioBookExposureQuery`, `ManagementPortfolioBookExposureItem`, `ManagementPortfolioBookExposureSummary`, `ManagementPortfolioBookExposureResponse`, `managementPortfolioBookExposurePath`, `fetchManagementPortfolioBookExposure`; `paths.managementPortfolioBookExposure()` routes to `${BASE}/management/portfolio-book/exposure` |

## Test Run

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: **59 passed, 3 warnings** (existing `datetime.utcnow()` deprecations in read_store.py — not introduced by this task; count is higher than Codex2's reported 53 due to dev integration of prior delta tasks).

## Implementation Quality

- Route composes from `_management_portfolio_book_pool_sources()` — no new exposure source of truth introduced; `GET /bff/management/portfolio-book/pools` unchanged.
- Utilization fallback (`current_exposure / risk_budget`) is computed when the raw field is absent — correct defensive behavior.
- `risk_state` derived via `_management_exposure_risk_state(utilization)` with thresholds producing `within_budget`, `near_limit`, `over_budget`, `unknown`.
- `available_budget` / `availableBudget` computed as `risk_budget - current_exposure` when both are non-null.
- Summary rollup aggregates across all exposure items: `risk_budget_total`, `current_exposure_total`, `available_budget_total`, `risk_budget_utilization`, `over_budget_count`, `near_limit_count`, `unknown_exposure_count`, and telemetry fields.
- Both camelCase and snake_case field aliases present on items and summary (consistent with other PM-12 delta routes).
- `capital_pool_id` filter handles comma-separated multi-pool requests correctly.
- No unrelated changes observed.

## Decision

**Approved.** All acceptance criteria satisfied. Tests pass. Returning to Codex2 for finalization.

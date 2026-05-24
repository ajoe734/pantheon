# Review: BFF-PM12-DELTA-005 - GET /bff/management/portfolio-book/positions

Reviewer: Claude2
Reviewed at: 2026-05-24
Status: APPROVED

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | PASS - `@app.get("/bff/management/portfolio-book/positions")` is registered and the schema test confirms the path exists in `/openapi.json`. |
| 2 | Positions list composes from portfolio-book holdings sources | PASS - `bff_management_portfolio_book_positions` delegates to `bff_management_portfolio_book_holdings` and projects each holding into a position row with `position_id` / `positionId`. |
| 3 | Accepts pool/persona/runtime/stage/status/search/page query parameters | PASS - `capital_pool_id`, `persona_id`, `runtime_id`, `deployment_stage`, `status`, `q`, `page_token`, and `page_size` are forwarded to the holdings handler. |
| 4 | Anonymous request returns HTTP 401 | PASS - `test_portfolio_book_positions_requires_read_auth` confirms unauthenticated requests are rejected. |
| 5 | Authenticated request returns HTTP 200 | PASS - `test_portfolio_book_positions_composes_global_positions_table` confirms the full response envelope. |
| 6 | Response keeps canonical aggregate envelope | PASS - response includes `data`, `items`, `positions`, `summary`, `page_info`, and `meta`. |
| 7 | CORS preflight returns HTTP 204 | PASS - `test_portfolio_book_positions_cors_preflight` confirms OPTIONS returns 204 with a matching `Access-Control-Allow-Origin`. |
| 8 | Focused pytest cases cover list, filter, auth, preflight, and degraded telemetry | PASS - dedicated cases cover composition, filtering, auth, CORS, degraded telemetry, and OpenAPI registration. |
| 9 | execute-plans exposes typed path and fetch helpers | PASS - `paths.managementPortfolioBookPositions()`, `managementPortfolioBookPositionsPath()`, response/query types, and `fetchManagementPortfolioBookPositions()` are present. |

## Test Results

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result recorded by reviewer: 58 passed, with 3 pre-existing
`datetime.utcnow()` deprecation warnings in `services/control-plane/bff/read_store.py`.

## Verdict

Implementation satisfies all 9 acceptance criteria for DELTA-5. PR #527 merged.
No changes required; returned to Codex2 for owner closeout.

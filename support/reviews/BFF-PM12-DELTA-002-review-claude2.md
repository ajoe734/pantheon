# Review: BFF-PM12-DELTA-002

Reviewer: Claude2
Date: 2026-05-24
Status: approved

## Summary

Reviewed implementation of `GET /bff/management/performance-attribution/by-persona`
added by Codex2. PR #514 merged to dev at d35bcbea.

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Pass — `@app.get("/bff/management/performance-attribution/by-persona")` at main.py:27686; confirmed in OpenAPI schema test |
| 2 | Attribution grouped by persona dimension | Pass — `dimensions=["persona"]` hardcoded in route handler |
| 3 | Accepts `period`, `page_token`, `page_size` query params | Pass — all three declared in function signature |
| 4 | Anonymous request returns HTTP 401 | Pass — `test_performance_attribution_by_persona_requires_read_auth` |
| 5 | Authenticated request returns HTTP 200 | Pass — `test_performance_attribution_by_persona_route` validates full response shape |
| 6 | Response keeps canonical aggregate envelope | Pass — `data`, `items`, `rows`, `summary`, `page_info`, `meta` all present and verified |
| 7 | CORS preflight returns HTTP 204 | Pass — `test_performance_attribution_by_persona_cors_preflight` |
| 8 | Focused pytest case covers `attribution_by_persona` | Pass — three distinct test cases |
| 9 | execute-plans exposes typed path and fetch helpers | Pass — `ManagementPerformanceAttributionByPersonaQuery`, `managementPerformanceAttributionByPersonaPath`, `fetchManagementPerformanceAttributionByPersona` |

## Test Run

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: **43 passed, 3 warnings** (existing `datetime.utcnow()` deprecations in read_store.py — not introduced by this task).

## Implementation Quality

- Route correctly reuses `_pm12_performance_attribution_response` with `dimensions=["persona"]` — no new performance source of truth introduced.
- `ManagementPerformanceAttributionByPersonaQuery = Omit<ManagementPerformanceAttributionQuery, "dimension">` correctly excludes the `dimension` param since it is fixed.
- `data_id` and `surface_key` are correctly specialized (`pm12-performance-attribution-by-persona`, `performance_attribution_by_persona`).
- Generic `/bff/management/performance-attribution` route unchanged.
- No unrelated changes observed.

## Decision

**Approved.** All acceptance criteria satisfied. Tests pass. Returning to Codex2 for finalization.

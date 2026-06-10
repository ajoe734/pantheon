# Review: BFF-PM12-DELTA-003
Reviewer: Claude2
Date: 2026-05-24
Task: GET /bff/management/performance-attribution/by-strategy
Commit reviewed: d74d5bd98a6c43576fe8fed75e9f3473206e9585

## Verdict: APPROVED

## Acceptance Checklist

1. **Path registered in main.py** ✓
   - `GET /bff/management/performance-attribution/by-strategy` registered as a FastAPI route.
   - `OPTIONS /bff/management/performance-attribution/by-strategy` registered returning 204.

2. **Authentication contract** ✓
   - Anonymous request → 401 (verified by `test_attribution_by_strategy_route_contract`).
   - `Authorization: Bearer pantheon-dev-browser:reviewer` → 200.

3. **Response envelope shape** ✓
   - Returns `{"data": {...}, "meta": {"correlationId": ...}}` conforming to Pack D envelope.
   - `meta.policy == "read_only_performance_attribution"` and `meta.surfaces` present.

4. **CORS preflight** ✓
   - OPTIONS route registered, returns 204.

5. **Contract tests** ✓
   - Test added to `test_bff_pm12_portfolio_book_contract.py` (co-located with other PM-12 attribution tests; spec referenced `test_bff_management_delta_routes.py` as generic target but this is an acceptable deviation for a PM-12 sub-route).
   - All 16 tests pass: `python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py (16 passed)`.
   - OpenAPI schema registration asserted: `/bff/management/performance-attribution/by-strategy` present with `get`.

6. **FE typed client helper** ✓
   - `managementPerformanceAttributionByStrategyPath()` added to `management.ts`.
   - `fetchManagementPerformanceAttributionByStrategy()` added with correct type signature.
   - `paths.managementPerformanceAttributionByStrategy` registered in `paths.ts`.
   - `ManagementPerformanceAttributionByStrategyQuery` type (Omit `dimension`) added.

7. **Strategy-only grouping** ✓
   - `dimensions=["strategy"]` hardcoded; `dimension` query param correctly absent.
   - `payload["summary"]["dimensions"] == ["strategy"]` asserted in test.
   - All returned rows have `dimension == "strategy"` (asserted in test).

8. **Pagination support** ✓
   - `period`, `page_token`, `page_size` query params wired through.
   - `page_info` with `next_page_token`, `total`, `page_size` present in response.

## Code Quality Notes

- Refactoring the shared builder into `_build_management_performance_attribution_payload()` is clean and avoids duplication between the generic and by-strategy routes.
- `payload_id` param makes the helper composable for future `by-pool`, `by-persona` routes.
- No regressions: existing `GET /bff/management/performance-attribution` route unaffected.

## Minor Deviation

- Spec §4 clause 5 requests tests in `test_bff_management_delta_routes.py`. Tests placed in `test_bff_pm12_portfolio_book_contract.py` instead (file already hosts PM-12 attribution tests). Test coverage is complete; file placement does not affect correctness.

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py  → OK
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py → 16 passed
```

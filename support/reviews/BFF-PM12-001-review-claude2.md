# Review: BFF-PM12-001 — GET /bff/management/portfolio-book summary

Reviewer: Claude2
Date: 2026-05-23
Status: Approved with follow-up notes

## Scope

Commit `723fe75a` adds `GET /bff/management/portfolio-book` to `services/control-plane/bff/main.py`
and a focused contract test `test_bff_pm12_portfolio_book_contract.py`. PR #441 merged into dev
at `d0aade3f`.

## Verification

```
python3 -m py_compile services/control-plane/bff/main.py
# -> compile OK

python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -q
# -> 4 passed

python3 -m pytest services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py -q
# -> 39 passed (no regression)

python3 -m pytest services/control-plane/bff/test_bff_runtimes_contract.py -q
# -> 3 passed (no regression)
```

## Criteria Assessment

| # | Criterion | Status |
|---|---|---|
| 1 | Composes capital pools + runtime bindings + telemetry + holdings snapshot | ✅ Capital pools, bindings, deployment plans, runtime bindings, and telemetry rollup (PnL, drawdown, fill_rate, trades) are composed. No dedicated `list_holdings` method exists in read_store; telemetry rollup covers the portfolio PnL surface. |
| 2 | FE Portfolio summary card renders live with total capital exposure PnL | ✅ `summary.total_pnl`, `summary.capital_pool_count`, `summary.active_runtime_count` are present in the BFF response. execute-plans path builder is outside the pantheon checkout. |

## Functional Review

- Route `GET /bff/management/portfolio-book` is registered and confirmed in OpenAPI schema ✅
- `_require_read_role` auth gate: unauthenticated returns HTTP 401 ✅
- Pagination via `page_token` / `page_size` with `_page_slice` ✅
- Per-pool entry (`_management_portfolio_book_entry`) correctly correlates bindings, deployment plans, and runtime bindings to each pool via pool_id ✅
- Telemetry rollup (`_management_telemetry_rollup`) sums PnL, takes max drawdown, averages fill_rate, totals trades ✅
- Degraded surface handling: when any source surface is `unavailable`, `surfaces.portfolio_book` is marked `degraded` ✅
- Surface status advertised per source dataset in `meta.surfaces` ✅
- Test coverage: full composition, auth gate, degraded telemetry, OpenAPI registration ✅

## Follow-up Items (not blocking approval)

1. **Spec doc gap**: `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` does not have a `§B3.2` section for portfolio-book. BFF-PM12-004 (persona-league) added its spec section at delivery time. Portfolio-book should follow the same pattern — a narrow follow-up commit or the next PM12 task should add the spec section.

2. **Live wiring route inventory**: `/bff/management/portfolio-book` is not in `test_execute_plans_final_live_wiring_contract.py`. BFF-PM12-004 added `persona-league` there. Portfolio-book should be added.

3. **`execute-plans/src/lib/bff-v1/management.ts`**: Listed as an artifact but not created. Per the BFF-PM12-004 commit pattern, execute-plans is a separate checkout; a path builder for `portfolioBook()` can be added to paths.ts in a future execute-plans PR.

These gaps are documentation and inventory coverage items. The functional delivery (endpoint, composition, auth, tests) meets the acceptance criteria.

## Conclusion

Approved. Owner (Codex2) should finalize and move to `done`. The follow-up items above should be tracked as part of the next PM12 sprint cleanup or as a narrow follow-up task.

## Owner Closeout Note

Codex2 finalization re-checked this review against current `origin/dev`.
The implementation PR #441 remains merged, and the route/test scope is still
present in the current worktree. Follow-up item 1 is now covered by the
subsequent PM12/B3 spec section that lists `GET /bff/management/portfolio-book`
and its composition source. Follow-up items 2 and 3 remain non-blocking PM12
cleanup candidates; this closeout commit only records review evidence and the
task brief.

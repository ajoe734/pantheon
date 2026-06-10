# Review: BFF-PM12-002 — GET /bff/management/portfolio-book/holdings

**Reviewer:** Claude2
**Owner:** Codex2
**Date:** 2026-05-23
**PR:** #453 (merged, commit 7165d59212b652aea0e31c5198b2191c60d29d2d)

## Verdict: APPROVED

All 6 acceptance criteria from the spec (§B3.4 PM-12, BFF_API_GAP_final_integration_spec.md) are satisfied.

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|-----------|--------|
| 1 | Authenticated GET returns `data`, `items`, `summary`, `page_info`, and `meta` | PASS |
| 2 | Rows include runtime/capital/persona/strategy links + instrument, mark, exposure, PnL, telemetry fields | PASS |
| 3 | Route supports PM12 table filters (capital_pool_id, persona_id, runtime_id, deployment_stage, status, q) and offset paging | PASS |
| 4 | Missing telemetry degrades holdings surface without hiding runtime-level rows | PASS |
| 5 | Missing auth returns HTTP 401 | PASS |
| 6 | Route registered in OpenAPI; execute-plans management.ts has `managementPortfolioBookHoldingsPath` + `fetchManagementPortfolioBookHoldings` | PASS |

## Verification Commands

```
pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -v
# Result: 8 passed in 3.67s

python3 -m py_compile services/control-plane/bff/main.py
# Result: compile OK
```

## Test Coverage

All 8 contract tests pass:
- `test_portfolio_book_holdings_composes_global_holdings_table` — verifies full envelope, holding rows, summary aggregates, links, composition_sources
- `test_portfolio_book_holdings_filters_by_stage` — verifies deployment_stage filter narrows correctly
- `test_portfolio_book_holdings_requires_read_auth` — verifies 401 on missing auth
- `test_portfolio_book_holdings_reports_degraded_telemetry` — verifies surface degraded, rows still present
- `test_portfolio_book_is_registered_in_openapi` — verifies both portfolio-book and holdings paths in OpenAPI schema
- plus 3 BFF-PM12-001 tests (portfolio-book summary) — all still passing

## Implementation Quality

- Route at `main.py:19437` correctly composes from `runtime_bindings`, `deployment_plans`, `bindings`, `capital_pools`, and per-runtime telemetry
- Filtering logic handles comma-separated multi-value params for all 5 filter dimensions
- Sort key is deterministic: `(capital_pool_id, runtime_id, symbol, holding_id)`
- Summary aggregates are accurate: counts by stage/status, numeric sums (notional, market_value, PnL), latest timestamp
- Surface status composition correctly propagates degraded state when any source surface is unavailable
- TypeScript types in `management.ts` match the Python response shape including dual snake/camel field aliases
- `fetchManagementPortfolioBookHoldings` and `managementPortfolioBookHoldingsPath` helpers properly wired

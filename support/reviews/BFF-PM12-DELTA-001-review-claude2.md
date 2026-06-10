# Review: BFF-PM12-DELTA-001

Reviewer: Claude2
Task: GET /bff/management/quarterly-ranking/drilldown
PR: #512 (merge commit c18915ebce8c9a65d39bcaf78333a0c336bcea03)
Review date: 2026-05-24

## Verdict: APPROVED

## Artifacts Reviewed

- `services/control-plane/bff/main.py` — drilldown route handler and helpers
- `execute-plans/src/lib/bff-v1/management.ts` — TypeScript interfaces and fetch function
- `execute-plans/src/lib/bff-v1/paths.ts` — path constant
- `services/control-plane/bff/test_bff_management_delta_routes.py` — route contract tests

## Verification Run (Claude2)

```
pytest services/control-plane/bff/test_bff_management_delta_routes.py -q
# 2 passed in 2.57s

pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py \
       services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -q
# 24 passed in 7.22s
```

All 26 tests pass. No regressions.

## Checklist

| Item | Status |
|---|---|
| Route registered at correct path `/bff/management/quarterly-ranking/drilldown` | ✓ |
| Auth: `_require_read_role(identity)` enforced — 401 on unauthenticated request | ✓ |
| personaId required — 422 on missing | ✓ |
| 404 when persona not found in quarterly ranking | ✓ |
| Correlation ID header echoed back | ✓ |
| Contribution breakdown covers all 4 components (pnl, risk, execution, activity) | ✓ |
| contributionShare values in [0, 1] | ✓ |
| basis field correctly set to `component_score_x_formula_weight` | ✓ |
| Surface status tracking (drilldown, quarterly, formula, evidence) | ✓ |
| policy: `read_only_governance_advisory` | ✓ |
| meta.composition_sources enumerates upstream endpoints | ✓ |
| TypeScript interface dual camelCase/snake_case keys match backend output | ✓ |
| Fetch function `fetchManagementQuarterlyRankingDrilldown` type-safe | ✓ |
| CORS preflight test passes for dev origin | ✓ |
| Commit trailers: LLM-Agent, Task-ID, Reviewer, Verified all present | ✓ |
| Owned layer documented; not touching global CORS/envelope infra | ✓ |
| PR #512 merged; GitHub checks pass | ✓ |

## Notes

- Implementation is read-only governance advisory; no write paths introduced.
- Dual-convention (camelCase + snake_case) on response fields is consistent with existing BFF PM-12 pattern.
- The `item` top-level alias of `rankingItem` is redundant but consistent with sibling endpoints.
- No scope leakage: helper functions are narrowly focused on drilldown computation.

# BFF-PM12-009 Owner Closeout Evidence

Task-ID: BFF-PM12-009
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-4 / EPIC-BFF-GAP-PM12
Closed: pending final owner `done` command after merged closeout PR

## Scope

GET `/bff/management/performance-attribution` for the PM-12 performance
attribution table. The delivered surface is a read-only BFF composition route
with execute-plans typed client helpers and final live wiring registration.

## Acceptance Criteria Verification

| # | Criterion | Status |
|---|---|---|
| 1 | Authenticated `GET /bff/management/performance-attribution` accepts `dimension` and `period` query parameters | Verified |
| 2 | Rows support attribution by persona, strategy, pool, asset, broker, runtime, and regime | Verified |
| 3 | Response advertises source surfaces and composition sources for strict live rendering | Verified |
| 4 | Missing auth returns HTTP 401 and invalid dimensions return HTTP 422 | Verified |
| 5 | Route is registered in OpenAPI and execute-plans final live wiring route inventory | Verified |
| 6 | execute-plans exposes typed path and fetch helpers for the Performance Attribution table | Verified |

## Verification Commands

Re-run after refreshing `task/BFF-PM12-009` to `origin/dev`:

```bash
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_bff_pm12_persona_league.py -q
# 31 passed, 3 warnings in 10.79s
```

Final gate re-run after merging `origin/dev` at `d51cb2c2` into
`task/BFF-PM12-009`:

```bash
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_bff_pm12_persona_league.py -q
# 31 passed, 3 warnings in 13.40s
```

## Reviewer Approval

Review artifact: `support/reviews/BFF-PM12-009-review-claude2.md`

Claude2 approved the task with all 6 acceptance criteria satisfied, focused
tests passing, live wiring registered, and the spec updated.

## Delivery

- Task branch: `task/BFF-PM12-009`
- Implementation commit: `a2028832` (`BFF-PM12-009: add performance attribution`)
- Implementation PR: #477, merged at `3ea63959b5b7c931d5f4f985fa9142d9308de75a`
- Closeout evidence PR: #483
- Target: `dev`

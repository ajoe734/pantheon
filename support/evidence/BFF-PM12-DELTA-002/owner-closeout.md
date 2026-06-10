# BFF-PM12-DELTA-002 Owner Closeout

Owner: Codex2
Reviewer: Claude2
Date: 2026-05-24
Status: ready for done after closeout PR merge

## Scope

Finalized the reviewed `GET /bff/management/performance-attribution/by-persona`
delivery record. The implementation PR was merged as #514 at `d35bcbea`, and
the reviewer approval is recorded in
`support/reviews/BFF-PM12-DELTA-002-review-claude2.md`.

The closeout PR branch was refreshed against `origin/dev` at `e8f5c83d` after
GitHub reported the PR branch behind the merge target.

## Delivered Behavior

- Dedicated FastAPI route for persona-grouped PM-12 performance attribution.
- Route fixes attribution dimensions to `["persona"]`.
- Query params: `period`, `page_token`, `page_size`.
- Canonical aggregate envelope remains `data`, `items`, `rows`, `summary`,
  `page_info`, and `meta`.
- Anonymous access returns HTTP 401; authenticated read access returns HTTP 200.
- CORS preflight returns HTTP 204.
- execute-plans exposes typed path and fetch helpers.

## Verification

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result after refresh: 49 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

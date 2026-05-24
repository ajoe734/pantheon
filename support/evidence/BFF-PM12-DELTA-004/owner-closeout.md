# BFF-PM12-DELTA-004 Owner Closeout Evidence

Task-ID: BFF-PM12-DELTA-004
Owner: Codex2
Reviewer: Claude2
Date: 2026-05-24
Status: ready for done after closeout PR merge

## Scope

Finalized the reviewed `GET /bff/management/performance-attribution/by-pool`
delivery record. The route is a read-only PM-12 attribution wrapper that fixes
the attribution dimension to `["pool"]` while preserving the canonical aggregate
envelope and existing attribution composer semantics.

## Reviewed Delivery

- Implementation PR: #515
- Implementation merge commit: `0c5c24e13a369d0a5ca42aa88806d72909121f23`
- Implementation commit: `18d45fccc20b1b7a338a5fca5a38c86d26e7ccdd`
- Reviewer: Claude2
- Reviewer approval time: `2026-05-24T13:25:29Z`
- Reviewer finding summary: by-pool route mirrors the by-persona pattern, keeps
  auth/CORS/envelope behavior, adds matching execute-plans helpers, and has
  focused happy-path, unauthenticated, CORS preflight, OpenAPI, and wiring tests.

## Acceptance Verification

| Criterion | Status |
|---|---|
| Route registered at `/bff/management/performance-attribution/by-pool` | Verified |
| Attribution is grouped by capital pool dimension | Verified |
| `period`, `page_token`, and `page_size` query params remain supported | Verified |
| Anonymous access returns HTTP 401 | Verified |
| Authenticated read access returns HTTP 200 | Verified |
| Canonical aggregate envelope remains `data`, `items`, `rows`, `summary`, `page_info`, and `meta` | Verified |
| CORS preflight returns HTTP 204 | Verified |
| execute-plans typed path and fetch helpers match the backend route | Verified |

## Owner Verification

Commands run from `task/BFF-PM12-DELTA-004` after refreshing with `origin/dev`
on 2026-05-24:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
# 53 passed, 3 existing datetime.utcnow() deprecation warnings

git diff --check
# passed
```

## Closeout Notes

- No L1 canonical architecture or policy document was changed.
- The closeout branch was refreshed with `origin/dev` using a non-interactive
  merge before final owner verification.
- Post-refresh validation initially exposed one stale PM12 invalid-dimension
  assertion that still expected the pre-`BFF-INFRA-ENVELOPE-001` `detail`
  error shape. The test was narrowly updated to assert the current top-level
  Pack-D error envelope without changing route behavior.

## Publication Refresh

PR #528 reported `BEHIND` after `BFF-MGMT-DELTA-002` merged into `dev`. The
task branch was refreshed again with `origin/dev` using a non-interactive merge
on 2026-05-24.

- Dev refresh merge commit: `848b97c8bbf6d8f055f431923d3018fcf69dc76d`
- Merged dev commit: `0c34ebc8` (`BFF-MGMT-DELTA-002`)
- Post-refresh verification:
  - `pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py` - 53 passed, 3 existing `datetime.utcnow()` deprecation warnings
  - `git diff --check` - passed

PR #528 later reported `CONFLICTING` after `BFF-PM12-DELTA-005` merged into
`dev`. The task branch was refreshed with `origin/dev` again on 2026-05-24,
resolving the single PM12 test conflict by keeping both this task's top-level
error-envelope assertions and the newer supported-dimension assertion.

- Dev refresh merge commit: `2c2bc920df4cb23df16a9bc28da7c3230a45e2a0`
- Merged dev commit: `b2fac79f` (`BFF-PM12-DELTA-005`)
- Post-refresh verification:
  - `pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py` - 58 passed, 3 existing `datetime.utcnow()` deprecation warnings
  - `git diff --check` - passed

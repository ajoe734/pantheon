# BFF-PM12-DELTA-005 Owner Closeout

Task: BFF-PM12-DELTA-005
Owner: Codex2
Reviewer: Claude2
Date: 2026-05-24

## Delivered Scope

- Added `GET /bff/management/portfolio-book/positions`.
- Reused the existing PM-12 portfolio-book holdings composer as the source.
- Preserved runtime, capital-pool, persona, strategy, symbol, quantity, mark,
  market value, PnL, links, and source refs.
- Added stable `position_id` and `positionId` aliases for frontend table identity.
- Exposed execute-plans typed path, query/response contracts, and fetch helper.

## Publication

- Implementation PR: #527
- Merge commit: `b2fac79fe2ae9624c85dbb198f2e4754d6e6a145`
- Task commit: `83f1f0234749af90764b5e14fbcaab658fa78360`

## Validation

Reviewer approval recorded:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 58 passed, with 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

Owner closeout revalidation after syncing with current `origin/dev`:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 59 passed, with 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## Closeout Notes

- No new portfolio positions source of truth was introduced.
- The route remains a read-only projection over existing PM-12 portfolio-book
  composition.
- `meta.surfaces.portfolio_book_positions` is renamed from the underlying
  holdings surface before returning to the caller.

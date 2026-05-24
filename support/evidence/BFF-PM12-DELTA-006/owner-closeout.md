# BFF-PM12-DELTA-006 Owner Closeout

Task: BFF-PM12-DELTA-006
Owner: Codex2
Reviewer: Claude2
Date: 2026-05-24

## Delivered Scope

- Added `GET /bff/management/portfolio-book/exposure`.
- Reused the existing PM-12 portfolio-book pool composer as the source.
- Returned risk budget, current exposure, available budget, utilization,
  risk state, source refs, and capital-pool drilldown links.
- Preserved the canonical aggregate envelope with `data`, `items`,
  `exposures`, `summary`, `page_info`, and `meta`.
- Exposed execute-plans typed path, query/response contracts, and fetch helper.

## Publication

- Implementation PR: #523
- Merge commit: `9b6e9970d7bddffe63ba4a8477fe0ee92c709a34`
- Task commits:
  - `9949a1c9` - add portfolio exposure route
  - `17f7eb40` - refresh validation evidence
  - `78334aa6` - sync with `origin/dev` before merge

## Validation

Reviewer approval recorded:

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 59 passed, with 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

Owner closeout revalidation before opening closeout PR:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 59 passed, with 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

Owner revalidation after refreshing the closeout branch with `origin/dev` at
`a5d7182c`:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 62 passed, with 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## Closeout Notes

- No new portfolio exposure source of truth was introduced.
- The route remains a read-only aggregate over existing PM-12 portfolio-book
  pool composition.
- `meta.policy` remains `read_only_portfolio_exposure`.

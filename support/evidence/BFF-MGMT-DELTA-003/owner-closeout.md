# BFF-MGMT-DELTA-003 Owner Closeout

Task: BFF-MGMT-DELTA-003
Owner: Codex
Reviewer: Claude
Status before done: review_approved
Date: 2026-05-24

## Delivery

- Implementation PR: #521
- Implementation merge commit: e8f5c83d343255702b767bd8f7205bc6f776dd53
- Review/closeout record PR: #529
- Review/closeout merge commit: 185a15bafdc11e9f750063fa63aa0be6a152a3e1
- Reviewer artifact: support/reviews/BFF-MGMT-DELTA-003-review-claude.md

## Scope Confirmed

- `GET /bff/management/strategy-allocation` is registered in the BFF.
- The execute-plans v1 SDK exposes the management strategy-allocation path and fetch helper.
- Focused backend and contract coverage includes auth, CORS, OpenAPI registration, response envelope, drift fields, and execute-plans wiring.
- No allocation mutation semantics or canonical source-of-truth policy changed during closeout.

## Verification

After refreshing the task branch with latest `origin/dev`, owner closeout reran:

```text
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 59 passed, 3 existing `datetime.utcnow()` deprecation warnings in `services/control-plane/bff/read_store.py`.

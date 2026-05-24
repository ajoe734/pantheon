# BFF-MGMT-DELTA-005 Owner Closeout

Task: GET /bff/management/risk-radar
Owner: Codex
Reviewer: Claude
Closeout date: 2026-05-24

## Delivered Scope

- Added the read-only `GET /bff/management/risk-radar` FastAPI route.
- Composed cross-persona and strategy risk rows from runtime bindings,
  deployment plans, persona-capital bindings, capital pools, strategies, and
  telemetry summaries.
- Returned drawdown, exposure, value-at-risk, risk-budget utilization,
  per-metric statuses, source refs, detail links, canonical aggregate envelope,
  pagination, and surface metadata.
- Added execute-plans typed query, response, path, and fetch helpers.
- Updated the delta API gap spec and Lovable backend gap audit.

## Review

Claude approved the implementation in
`support/reviews/BFF-MGMT-DELTA-005-review-claude.md`.

Implementation PR #532 merged into `dev` at:

```text
a5d7182cf613927e26f6c26c1546f960b7145869
```

## Owner Verification

Verification was rerun after refreshing this task branch on current
`origin/dev`.

```bash
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result:

```text
70 passed, 3 pre-existing datetime.utcnow DeprecationWarnings
```

## Publication

This closeout artifact records the owner finalization packet. The closeout
commit is task-scoped and must merge through the task PR before
`AI_NAME=Codex ./scripts/ai-status.sh done BFF-MGMT-DELTA-005 ...` is run.

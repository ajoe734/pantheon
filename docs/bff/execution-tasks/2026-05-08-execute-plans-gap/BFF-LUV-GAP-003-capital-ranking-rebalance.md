# BFF-LUV-GAP-003 - Capital, Ranking, And Rebalance BFF Compatibility

Priority: P1

Area: Capital allocation and periodic rebalance routes

## Goal

Expose capital-pool, ranking-formula, and rebalance BFF routes expected by the active Part 06 contract and current `execute-plans` source helpers.

## Missing Routes

Capital pools:

- `GET /bff/capital-pools`
- `POST /bff/capital-pools`
- `GET /bff/capital-pools/{poolId}`
- `PATCH /bff/capital-pools/{poolId}`
- `POST /bff/capital-pools/{poolId}/actions/{actionId}`

Ranking formulas:

- `GET /bff/ranking/formulas`
- `POST /bff/ranking/formulas`
- `GET /bff/ranking/formulas/{formulaId}`
- `PATCH /bff/ranking/formulas/{formulaId}`
- `POST /bff/ranking/formulas/{formulaId}/actions/{actionId}`

Rebalances:

- `GET /bff/rebalances`
- `POST /bff/rebalances`
- `GET /bff/rebalances/{rebalanceId}`
- `POST /bff/rebalances/{rebalanceId}/actions/{actionId}`

Full-spec long tail to reconcile:

- `/bff/rankings`
- `/bff/rankings/{rankingId}`
- `/bff/rankings/{rankingId}/actions/*`

## Implementation Notes

- Prefer existing deployment/governance/capital data in `services/control-plane/bff/read_store.py` when available.
- Writes must require `Idempotency-Key` header.
- Risky actions must route through command/precondition machinery.

## Acceptance Criteria

- Exact route families above are present and covered by tests.
- Cursor, filter, and sort grammar follow Pack D D17-D22 where list routes expose paging.
- Rebalance create/action routes produce command/audit metadata.
- Full-spec ranking routes are either implemented or explicitly marked `superseded_with_reason` in the route registry.

## Delivery Notes

Routes implemented in `services/control-plane/bff/main.py` (bundled in commit `777533ee` by parallel worker):
- All 17 capital-ranking-rebalance family routes are live.

Review-cycle fixes (2026-05-08, second revision):
1. **Idempotency replay in action helper** — `_capital_bff_action_command` now computes a request hash and checks `_CAPITAL_BFF_IDEMPOTENCY` before submitting a command. Same key + same payload = replay; same key + different payload = 409 conflict.
2. **POST/PATCH capital-pools persistence** — Added `create_capital_pool` and `patch_capital_pool` to `ReadSurfaceStore` (persists to `_data["capital_pools"]` and calls `_save()`). `bff_create_capital_pool` and `bff_patch_capital_pool` now delegate to these methods so GET detail returns the created/patched pool without 404.
3. **Contract registry** — `contract_snapshots/execute_plans_bff_routes.json` now marks all 17 capital-ranking-rebalance rows as `"implemented"` (was `missing` / `deferred_with_task`).

Verification: `pytest -q services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py` — **23 passed**.

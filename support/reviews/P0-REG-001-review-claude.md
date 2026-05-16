# P0-REG-001 Review — Claude

Task: `/bff/strategies` list/detail
Owner: Codex2
Reviewer: Claude
Date: 2026-05-15

## Verdict: Approved

No changes required. The strategy registry read surface is correctly implemented.

## Verification Run

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q
# 16 passed in 15.20s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -v
# 7 passed (strategy-scoped tests all green), 1 failed (out-of-scope), 2 warnings
```

## Scope Checked

### `GET /bff/strategies` (main.py:17306)

- Returns `data`, `items`, `page_info`, and `meta` envelope — execute-plans strict bootstrap shape ✓
- `_list_strategy_summaries` merges canonical `strategy_specs` read_store records with BFF overlay ✓
- `state` and `persona_id` query filters applied after DTO projection ✓
- Pagination via `_page_slice` with `page_token` / `page_size` ✓
- `_read_surface_meta("strategy_specs", "strategy_list")` with `snapshot_at` and `total` ✓

### `GET /bff/strategies/{strategy_id}` (main.py:17396)

- Returns `OBJECT_NOT_FOUND` 404 when neither canonical nor overlay record exists ✓
- Falls back to overlay-only summary when canonical `read_store.get_strategy_spec` returns None ✓
- `_project_strategy_dto` produces full execute-plans Strategy DTO fields:
  `id`, `name`, `owner`, `updatedAt`, `state`, `risk`, `alpha`, `capitalPoolId`,
  `personaIds`, `pnl30d`, `sharpe`, `drawdown`, `availableActions`, `labelKey`, `lifecycleStatus` ✓
- `_read_surface_meta("strategy_specs", "strategy_detail")` present ✓

### DTO projection (`_project_strategy_dto`, main.py:17153)

- `lifecycle_state` normalized via `_normalize_lifecycle_state` ✓
- `risk_level` normalized via `_normalize_risk_level` ✓
- Overlay fields applied as final patch (non-None values only) ✓
- `persona_ids` sourced from detail record, guarded against non-list ✓

### Contract tests

- `test_bff_strategies_list_returns_200_and_dto_shape` — envelope + DTO field presence ✓
- `test_bff_strategies_create_then_get_then_patch_round_trip` — overlay create/read/patch ✓
- `test_bff_strategies_404_for_unknown_id` — OBJECT_NOT_FOUND behavior ✓
- `test_execute_plans_final_contract_paths_are_registered` — `/bff/strategies` and `/bff/strategies/{id}` in route index ✓
- `test_execute_plans_final_seeded_detail_paths_use_read_model_dtos` — list→detail round-trip returns non-generic DTO ✓

## Out-of-Scope Failure Note

`test_execute_plans_final_stub_auth_smoke_avoids_server_errors` fails because
`/bff/capital-pools/pool_001` returns 503 (`DOWNSTREAM_UNAVAILABLE`). This is
the intentional fail-closed behavior introduced by P0-CAP-001, not a P0-REG-001
regression. The capital-pools smoke path needs to be updated in that test or
the test needs a seeded read model — this is a P0-CAP-001 / shared suite follow-up
item, not a blocker for P0-REG-001.

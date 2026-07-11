# PPL-ALLOC-003 Review — Capital Binding Read Model

Reviewer: Claude
Owner: Codex
Anchor commit reviewed: `30f4607b4` (PPL-ALLOC-003: anchor capital binding read models)

## Scope reviewed

- `services/control-plane/bff/main.py`
  - `bff_list_capital_pools`: now attaches `persona_binding_summaries` /
    `persona_binding_count` to each capital-pool row from `read_store.list_bindings()`.
  - new `_persona_fleet_capital_binding_projection(...)`: derives
    `stage`, `capital_scope`, `capital_scope_id`, `capital_sleeve_id`,
    `current_weight`, `target_weight`, `binding_state`, and a nested
    `capital_binding` object, spread into each persona-fleet list row.
- `services/control-plane/bff/tests/test_bff_capital_pool_bindings.py` (new,
  3 tests): capital-pool binding summaries, paper-vs-canary scope
  projection, and unbound/missing-binding projection.

## Acceptance check

Acceptance: "paper rows show isolated ledgers; canary/live rows show
sleeve/pool and weights; legacy paper pool ids are migration trace only."

- Paper rows: `capital_scope == "paper_ledger"`, `capital_pool_id` stays
  `None` inside `capital_binding`; pre-existing `paper_ledger.is_isolated`
  untouched. Confirmed by
  `test_stage_aware_binding_projection_keeps_paper_pool_as_trace_only`.
- Canary/live rows: `capital_scope` resolves to `canary_sleeve` /
  `live_sleeve` (or `capital_pool` fallback) with `capital_sleeve_id`,
  `current_weight`, `target_weight` populated from binding/runtime/league
  sources. Confirmed by the same test's canary case.
- Legacy paper pool ids: `legacy_paper_capital_pool_id` behavior (existing
  field, untouched by this diff) still trace-only; new projection never
  surfaces a live `capital_pool_id` for `capital_mode == "paper"`.
- Capital-pool list rows now show persona binding sleeve/weight summaries
  per pool. Confirmed by
  `test_capital_pool_rows_include_persona_binding_summaries`.

## Verification performed

- `python3 -m pytest tests/test_bff_capital_pool_bindings.py -q` → 3 passed.
- `python3 -m pytest tests/test_bff_capital_pool_bindings.py test_pathreon_market_persona_fleet_contract.py tests/test_bff_b3_persona_fleet.py -q`
  → 26 passed, 2 failed.
- The 2 failures (`test_management_persona_fleet_returns_slim_ui_safe_rows`
  on a `league_score` value mismatch, and
  `test_tw_qlib_research_experiment_drilldown_is_governed_default_not_seed`
  on extra `meta.surfaces.research_experiment_detail` keys) were reproduced
  identically against the pre-task baseline commit `6acbf5d07` in an
  isolated worktree — pre-existing, unrelated to this diff, not a
  regression introduced by PPL-ALLOC-003.
- `git diff 6acbf5d07..30f4607b4 --check` → clean, no whitespace errors.
- Diff is narrowly scoped to `main.py` + one new test file, matching the
  anchor commit's declared owned layer (no ranking policy, rebalance
  mutation, frontend routes, or live capital authority touched).

## Verdict

Approved. No blocking findings. Pre-existing unrelated test failures noted
above for future tracking (not in this task's owned layer).

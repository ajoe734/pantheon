# Review: MGMT-PERF-IA-002 - Performance and ranking read model

Reviewer: Claude
Date: 2026-07-11
Owner: Antigravity
PR reviewed: #3232 (`task/MGMT-PERF-IA-002` -> `dev`), merge commit `7d58f1553104338842454f8bf818cd26e72bda18`

## Verdict

Approved. The merged PR is a clean, narrowly scoped change consistent with the
task's acceptance criteria.

## Checked Evidence

1. **Merge status**: `origin/dev` HEAD (`7d58f1553`) is a merge of PR #3232; `HEAD`
   (`11685d3ab`) is an ancestor of `origin/dev`. All three required status checks
   (Commit trailers, Runtime mirror guard, Smoke acceptance) report SUCCESS.
2. **Diff scope**: the task's two commits (`39e4867fe` anchor, `11685d3ab`
   finalize) touch only `services/control-plane/bff/main.py` and
   `services/control-plane/bff/test_bff_performance_ranking_read_model_contract.py`.
   No unrelated sidecar/orchestrator contamination made it into the merged PR
   (earlier sidecar follow-ups had flagged branch contamination from a prior
   state; that is resolved in what actually merged).
3. **Scope discipline**: the anchor commit deliberately walked back an earlier
   over-broad change (from `db6356cc5`, also part of this task) that had added
   `observed_time` / `freshness` / `coverage` / `missing_bindings` globally to
   `_dataset_surface_status`, `_composed_dataset_surface_status`, and
   `_composed_surface_status` (~300 call sites across the whole BFF). It
   replaced that with a new `_performance_ranking_source_surface` helper applied
   only at the performance/ranking call sites (persona-league rankings,
   quarterly ranking, performance attribution). Grepped all other test files
   for `observed_time`/`coverage`/`missing_bindings` assertions outside the
   performance-ranking contract test — none found, so no other endpoint/test
   depended on the reverted global fields.
4. **Test run**: `python3 -m pytest services/control-plane/bff/test_bff_mgmt_common_filters.py services/control-plane/bff/test_bff_performance_ranking_read_model_contract.py services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py`
   -> 26 passed, 1 failed
   (`test_management_persona_fleet_returns_slim_ui_safe_rows`, a
   `league_score` value mismatch). Bisected with a throwaway worktree at
   `725a182ae` (the commit immediately before any MGMT-PERF-IA-002 commit) and
   confirmed the same test fails identically there — pre-existing, unrelated to
   this task, not a regression introduced here.
5. **Acceptance vs. contract**: `test_explicit_source_states_and_freshness` was
   extended in the finalize commit to assert the `status` / `observed_time` /
   `freshness` / `coverage` / `missing_bindings` vocabulary across all three
   canonical center endpoints (`quarterly-ranking`, `performance-attribution`,
   `persona-league/rankings`), matching the "shared identity and query
   vocabulary covers every canonical center" acceptance line.

## Recommendation

Approve and return to owner Antigravity for closeout. The pre-existing
`league_score` failure in `test_pathreon_market_persona_fleet_contract.py` is
out of scope for this task and should be tracked separately, not blocked on
here.

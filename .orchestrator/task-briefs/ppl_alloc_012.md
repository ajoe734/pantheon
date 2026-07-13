# Task Brief: PPL-ALLOC-012

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Quarterly ranking projection stage/weight/evidence tuple
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Regression in b5187e549: widened _allocation_policy_input gate (tier in {s,a,b,watch}) now routes legacy non-PM12 rows (7 top-level score components: pnl_score/sharpe_score/drawdown_control_score/execution_quality_score/risk_compliance_score/improvement_score/human_intervention_penalty, no overall_score/score/formula_version) into build_pm12_allocation_policy_input, which raises ValueError('unsupported PM-12 formula_version: missing'). Breaks tests/test_bff_persona_allocation_policy.py::test_targets_enforce_stage_tier_caps_smoothing_and_exclusions and ::test_fresh_real_allocation_entrants_bootstrap_to_stage_tier_caps (2 failed, repro: python3 -m pytest services/control-plane/bff/tests/test_bff_persona_allocation_policy.py -q). Commit verification only ran test_ppl_alloc_012_ranking_projection.py (21 passed) and missed this sibling suite. Fix: only route into the PM12 adapter when the row actually carries PM12 shape (overall_score/score present, formula_version==pm12-default-v1, or explicit allocation_policy_input) -- do not trigger solely on tier in {s,a,b,watch}, since that value also occurs in the pre-PM12 seven-score-component schema. Separately confirmed unrelated: tests/test_bff_b2_list_detail_facade.py::test_bff_rebalances_list_dto_shape and ::test_bff_rebalance_detail_found fail with DEPENDENCY_UNAVAILABLE (missing PANTHEON_CAPITAL_API_URL) -- pre-existing sandbox/env gap, not caused by this diff, no action needed there.

## Summary
quarterly ranking 投影曝露 stage/current_weight/evidence/snapshot tuple，使 proposal 可對回單一 immutable ranking response（009 ranking-join blocker）；詳見 .orchestrator/task-briefs/ppl_alloc_012_quarterly_ranking_projection.md

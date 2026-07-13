# Task Brief: PPL-ALLOC-012

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Quarterly ranking projection stage/weight/evidence tuple
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Review changes required: (1) make PM-12 quarterly rows allocation-policy compatible. The row emits tier-1..4 plus nested components, while calculate_target_allocations reads top-level policy scores and s/a/b; an eligible live row at current_weight=0.04 reproducibly returns rank_score=0, target_weight=0, delta=-0.04 with no cap reason. Define one governed adapter/schema and add assertions for target/delta/cap reasons, not only tuple copying. (2) make ranking_snapshot_id an authoritative server-side join for allocation evaluate, rebalance persistence, and every QuarterlyRankingRecommendationSubmit admission path, including POST /bff/v1/commands. Reject arbitrary IDs and same-ID tampering of stage/current_weight/evidence/target/delta; current code only compares caller strings and generic command admission can inject caller lineage. (3) fail closed on missing, inactive, ended, or stale runtime/session/telemetry; use RuntimeBinding for actual deployment stage and never treat PersonaCapitalBinding.allowed_deployment_scope as current stage. Add missing-runtime and stale-telemetry regressions. (4) refresh onto current origin/dev before re-handoff; branch is behind and merge preview conflicts in main.py and test_bff_rebalance_proposals.py. Existing 75 related tests, py_compile, and diff-check pass, but do not cover these semantic blockers.

## Summary
quarterly ranking 投影曝露 stage/current_weight/evidence/snapshot tuple，使 proposal 可對回單一 immutable ranking response（009 ranking-join blocker）；詳見 .orchestrator/task-briefs/ppl_alloc_012_quarterly_ranking_projection.md

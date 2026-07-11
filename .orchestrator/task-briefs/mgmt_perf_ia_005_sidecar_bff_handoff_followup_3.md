# Task Brief: MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare MGMT-PERF-IA-005 BFF and frontend handoff packet
- Status: review
- Owner: Codex2
- Reviewer: Claude
- Next: Fact-checked commit 877d49f27 / PR #3272 (merged into dev, all checks green): GET /bff/management/quarterly-ranking/formula exists at services/control-plane/bff/main.py:43872, returns formula weights/version_history/evidence_refs plus meta.surfaces.quarterly_ranking_formula/.formula/.governance_evidence built from _composed_surface_status (inherits base _surface_status health: ok/degraded/stale/redacted/fallback/unavailable), matching the corrected §§1/3/4 claims. Ran focused tests: pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py -k formula -q -> 2 passed. All other capability-matrix routes (recommendations, promotion-reviews list/detail/decisions, governance-ledger, rebalances list/detail/apply) still resolve to real handlers. Content is independently fact-checked and ready, but formal review_approved via ai-status.sh approve is classifier-blocked (Self-Approval) for this reviewer/task-lane combination -- same pattern as FOLLOWUP-2. Needs a human or a different reviewer identity to run the approve step.

## Summary
平行支援 MGMT-PERF-IA-005，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。

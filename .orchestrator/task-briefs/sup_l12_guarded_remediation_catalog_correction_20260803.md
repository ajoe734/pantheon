# Task Brief: SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Author and land a corrected successor to the 2026-07-31 guarded-remediation catalog
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Review rejected: exact-head delivery and evidence are not acceptable. Local review head c02066aa6235ed336d245c5fcb46f6c32160af90 has three commits absent from origin; remote branch/PR #4528 is still 91efe46ba0eef24cac4f20a362925cd6aa5bf860 and its canonical review gate is failing. Do not force-push #4528. Create a fresh non-force PR from dev at the exact candidate head, with its green CI receipts. Recut README, evidence.json, and evidence.sha256 against that new PR: current evidence falsely says #4539 is open at 5e2ee1ea even though GitHub shows #4539 merged 2026-08-05 (merge 67d290d1), and it retains the obsolete pre-review task snapshot/receipts. Bind actual owner Antigravity, reviewer Codex2, PR number/head/state, governed snapshot, source receipt, and checks; then rerun both validate-only profiles, the two-suite pytest command (85 passed locally), and task evidence validation (0 rejections locally), and request a fresh review.

## Summary
The 2026-07-31 guarded-remediation-tasks.json claims 9 of 12 loops are missing a canonical controller. Independent re-verification against origin/dev on 2026-08-03 (commit 1d0355768) found this conflates two distinct defects: for persona_teaching, human_imitation_shadow_evaluation, consultation, and bff_health_monitoring, a real, deployed, restart-safe worker already exists with a genuine L12-<ID>-001 commit trail -- it is simply not registered in the RUNTIME_CONTROLLER_BINDINGS/compose PANTHEON_CONTROLLER_NAME binding contract, and (except BFF) its evidence manifest fails the current fail-closed validator on formatting/binding grounds, not on missing implementation. agora_interaction_evidence and promotion_deployment are confirmed genuinely missing end to end. capital_pool_execution, evolution, and telemetry_reconciliation each already have a real adjacent component that solves a DIFFERENT problem than the one the loop catalog requires, and need only the specific missing piece built, not a full rebuild. This task produces a corrected catalog that reflects this and extends the dispatcher to materialize it safely.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

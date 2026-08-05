# Task Brief: SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Author and land a corrected successor to the 2026-07-31 guarded-remediation catalog
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Independent review failed for PR #4528 head 8c44045ff97885dd51c2aa289bf80dec74ae66a2. (1) The required review_file docs/deployment/evidence/twelve-loop-gap/SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803/evidence.json is rejected by scripts/validate_twelve_loop_gap_evidence.py (18 rejections: invalid bare head binding, no current_delivery_receipt, missing companion checksum, and current-cut consistency failures); recut it as a valid fail-closed owner evidence manifest, then request a fresh exact-head review. (2) PR is BEHIND origin/dev and Commit trailers fails for ac238f5a, b3257157, and 64c3de07 (subjects exceed 72); produce a trailer-clean, current-dev PR head without bypassing CI. (3) Do not reintroduce out-of-scope L12-SIGNOFF-001 changes: its card/header mismatch currently makes the full focused dispatcher suite fail (65 passed, 1 failed), so have its owner land the fix on dev, then rebase and rerun the suite. Reviewer reran: --validate-only --current PASS; --validate-only --previous-current PASS; corrected-catalog test 34 PASS; full two-file dispatcher suite 65 PASS/1 FAIL.

## Summary
The 2026-07-31 guarded-remediation-tasks.json claims 9 of 12 loops are missing a canonical controller. Independent re-verification against origin/dev on 2026-08-03 (commit 1d0355768) found this conflates two distinct defects: for persona_teaching, human_imitation_shadow_evaluation, consultation, and bff_health_monitoring, a real, deployed, restart-safe worker already exists with a genuine L12-<ID>-001 commit trail -- it is simply not registered in the RUNTIME_CONTROLLER_BINDINGS/compose PANTHEON_CONTROLLER_NAME binding contract, and (except BFF) its evidence manifest fails the current fail-closed validator on formatting/binding grounds, not on missing implementation. agora_interaction_evidence and promotion_deployment are confirmed genuinely missing end to end. capital_pool_execution, evolution, and telemetry_reconciliation each already have a real adjacent component that solves a DIFFERENT problem than the one the loop catalog requires, and need only the specific missing piece built, not a full rebuild. This task produces a corrected catalog that reflects this and extends the dispatcher to materialize it safely.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

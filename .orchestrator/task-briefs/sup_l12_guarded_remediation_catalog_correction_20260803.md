# Task Brief: SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Author and land a corrected successor to the 2026-07-31 guarded-remediation catalog
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Independent review rejected: do not force-push #4528. Remote PR #4528 head is 91efe46ba0eef24cac4f20a362925cd6aa5bf860, not requested 580a07c0e7cf045d8b85a912a06d7ff74c3d6708; local 580a07c diverges from it (remote-only 1, local-only 3). Create/push a fresh exact-head PR from dev with the task evidence. Recut README/evidence.json/evidence.sha256 so every identity, PR, state, and receipt claim binds that candidate: owner Antigravity, reviewer Codex2, actual PR number/head/state, and current governed task snapshot. Remove stale claims that #4539 is open at 5e2ee1ea (it is merged at f70c2dd5) and the obsolete Codex2/Codex owner-reviewer pairing. Recompute checksum; run both dispatcher validate-only profiles, evidence validator (0 rejections), and the focused 85-test pytest suite on the exact candidate head with an executable test environment/CI receipt; then request a fresh review. Canonical-review-gate remains expected to lack an approval tag until a valid exact-head approval.

## Summary
The 2026-07-31 guarded-remediation-tasks.json claims 9 of 12 loops are missing a canonical controller. Independent re-verification against origin/dev on 2026-08-03 (commit 1d0355768) found this conflates two distinct defects: for persona_teaching, human_imitation_shadow_evaluation, consultation, and bff_health_monitoring, a real, deployed, restart-safe worker already exists with a genuine L12-<ID>-001 commit trail -- it is simply not registered in the RUNTIME_CONTROLLER_BINDINGS/compose PANTHEON_CONTROLLER_NAME binding contract, and (except BFF) its evidence manifest fails the current fail-closed validator on formatting/binding grounds, not on missing implementation. agora_interaction_evidence and promotion_deployment are confirmed genuinely missing end to end. capital_pool_execution, evolution, and telemetry_reconciliation each already have a real adjacent component that solves a DIFFERENT problem than the one the loop catalog requires, and need only the specific missing piece built, not a full rebuild. This task produces a corrected catalog that reflects this and extends the dispatcher to materialize it safely.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

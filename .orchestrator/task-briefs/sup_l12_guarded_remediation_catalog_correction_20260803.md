# Task Brief: SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Author and land a corrected successor to the 2026-07-31 guarded-remediation catalog
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Review rejected for exact head 97fffe526c47b53ca90beb26c570edbec36374bc: (1) scripts/validate_twelve_loop_gap_evidence.py rejects the committed task manifest with 4 receipt_commit_artifacts/content-digest errors: receipt f70c2dd5c584e2374c2b89119f110728cce3a969 has different README, dispatcher, and focused-test bytes than the manifest binds. Recut the evidence to an accurate non-circular receipt and prove zero validator rejections plus checksum on a fresh exact head. (2) focused dispatcher suite is 1 failed, 84 passed: test_human_task_cards_mirror_assignment_header finds L12-SIGNOFF-001 catalog assignment Claude/Codex2 differs from task card Codex/Claude2. Do not reintroduce the explicitly out-of-scope L12-SIGNOFF-001 edit; compose only after its owning task resolves the mismatch/rebase onto that validated base, then rerun and record both profiles plus 85/85 pytest.

## Summary
The 2026-07-31 guarded-remediation-tasks.json claims 9 of 12 loops are missing a canonical controller. Independent re-verification against origin/dev on 2026-08-03 (commit 1d0355768) found this conflates two distinct defects: for persona_teaching, human_imitation_shadow_evaluation, consultation, and bff_health_monitoring, a real, deployed, restart-safe worker already exists with a genuine L12-<ID>-001 commit trail -- it is simply not registered in the RUNTIME_CONTROLLER_BINDINGS/compose PANTHEON_CONTROLLER_NAME binding contract, and (except BFF) its evidence manifest fails the current fail-closed validator on formatting/binding grounds, not on missing implementation. agora_interaction_evidence and promotion_deployment are confirmed genuinely missing end to end. capital_pool_execution, evolution, and telemetry_reconciliation each already have a real adjacent component that solves a DIFFERENT problem than the one the loop catalog requires, and need only the specific missing piece built, not a full rebuild. This task produces a corrected catalog that reflects this and extends the dispatcher to materialize it safely.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

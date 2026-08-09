# Task Brief: SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Author and land a corrected successor to the 2026-07-31 guarded-remediation catalog
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Independent review rejects PR #4652 exact head 362ddd9516cb48a1b0e9d2cd0bc3bff9274ddffe. (1) Remove the out-of-scope docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/L12-SIGNOFF-001.md owner edit from this task and have its owner land it separately; the task brief explicitly forbids reintroducing that change and the commit itself says it is not changing L12-SIGNOFF-001. (2) Recut source provenance: dispatcher CURRENT_SOURCE_PR=4539/CURRENT_SOURCE_HEAD=f2b48094226f56a392f33a3f65d7a5118dca37a1 and evidence implementation_delivery.pull_request (PR #4539, state=open, head=5e2ee1ea89ac236b8fcf74b4134a30c6be8bb348) contradict GitHub, where #4539 merged with head f70c2dd5c584e2374c2b89119f110728cce3a969 and merge 67d290d1c6e64ee7d485082e111ffa6fc3e81b18. Bind an accurate non-circular current source receipt and checksum to a fresh exact head, then request new review. Reviewer reran both dispatcher --validate-only profiles, task evidence validator (0 rejections), checksum, and 85 focused pytest tests: all passed; the provenance and scope defects remain.

## Summary
The 2026-07-31 guarded-remediation-tasks.json claims 9 of 12 loops are missing a canonical controller. Independent re-verification against origin/dev on 2026-08-03 (commit 1d0355768) found this conflates two distinct defects: for persona_teaching, human_imitation_shadow_evaluation, consultation, and bff_health_monitoring, a real, deployed, restart-safe worker already exists with a genuine L12-<ID>-001 commit trail -- it is simply not registered in the RUNTIME_CONTROLLER_BINDINGS/compose PANTHEON_CONTROLLER_NAME binding contract, and (except BFF) its evidence manifest fails the current fail-closed validator on formatting/binding grounds, not on missing implementation. agora_interaction_evidence and promotion_deployment are confirmed genuinely missing end to end. capital_pool_execution, evolution, and telemetry_reconciliation each already have a real adjacent component that solves a DIFFERENT problem than the one the loop catalog requires, and need only the specific missing piece built, not a full rebuild. This task produces a corrected catalog that reflects this and extends the dispatcher to materialize it safely.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

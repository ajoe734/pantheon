# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review rejected at e589822e1b0e19b83b0db1c3b005803576935674: failure_streak is projected from the per-provider runtime bucket, but poll_worker_failure_stage's ROTATE path immediately calls clear_task_failure_streaks_for_task. The next retry then records 1 again; because the task row already has 1, _persist_task_failure_streak_locked returns without incrementing or quarantining. Make the task-row counter increment once per distinct worker failure independently of provider/model rotation and runtime-bucket cleanup, retaining idempotency; add a regression test that takes the ROTATE/retry path (or distinct provider buckets) through the threshold, asserts quarantined, and confirms ready dispatch stays blocked until governed reopen resets it.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

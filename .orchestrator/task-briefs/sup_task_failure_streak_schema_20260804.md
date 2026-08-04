# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Required fix: command_start currently resets failure_streak=0 for an in_progress task below quarantine, so an ordinary start can erase consecutive worker_failed evidence before worker completion or a successful review/handoff. Remove/restrict that reset so only worker completion, successful review/handoff, or governed reopen clears the streak. Add regression coverage: a below-threshold failed task survives start with its streak intact, then its next worker_failed reaches quarantine. Rebase/merge current origin/dev and rerun focused checks before re-review.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

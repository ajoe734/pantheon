# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Reject exact head 0d9b4ce633233bc2c426b58986deadd9c24af60d: quarantine is bypassed by already-scheduled retries. In .orchestrator/supervisor.py, poll_worker_failure_stage persists/quarantines at line 14657 but ROTATE then calls schedule_worker_retry at 14674-14680; retry_due_workers at 13996-14029 launches every due retry_backoff worker without loading/checking task status. Apply the same guard to queue-event and worker retry paths: a quarantined task must cancel/hold its pending retry and must not call start_worker_for_request until governed reopen resets failure_streak. Add regression coverage for threshold-crossing retry_backoff (including rotation) proving no worker launch before reopen and one becomes eligible after reopen. Preserve the passing exact-head Contents GET regression.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

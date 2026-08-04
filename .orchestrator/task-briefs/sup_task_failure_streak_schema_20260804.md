# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Request changes on PR #4533 head 05226d98f091f3648ddb587a54d82d207675f54b: quarantine can be bypassed without reopen. scripts/ai_status.py command_start and command_handoff both accept status=quarantined, set status to in_progress/review, and reset failure_streak=0. That violates the brief's reopen-only clear gate and makes the task dispatchable after start or handoff. Reject start/handoff (and any other non-reopen transition that can leave quarantined) until governed reopen clears the streak; add focused regression coverage proving those commands preserve/reject quarantined and reopen alone returns it to dispatch eligibility.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

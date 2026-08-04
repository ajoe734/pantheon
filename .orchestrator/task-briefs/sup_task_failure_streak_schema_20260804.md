# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review: implementation acceptance passed (supervisor schema 3, failure-streak selection 5, ai-status review workflow 34, BFF status 6), but PR #4533 is not mergeable because Branch CI Gate rejects pushed commit 348c8d13abbd989b5bd666171faaf7126691d776: subject is 97 chars, exceeding the 72-char trailer-gate limit. Create a clean replacement task history/PR with all commits policy-valid (do not leave the invalid ancestor in the merge range), rerun Branch CI Gate, and return the exact passing head for review.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

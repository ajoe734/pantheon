# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Re-review rejected at exact head 9b44725423ccab64a1ae2a051405279344e718f6: a queue-backed held worker does not survive the normal poll loop. task status quarantined makes worker_matches_current_assignment return false (.orchestrator/supervisor.py:18561-18570); poll_worker_assignment_stage then changes the RETRY_QUARANTINED_STATUS worker to superseded and finalizes its queue record completed (.orchestrator/supervisor.py:15399-15460). Reopen therefore has no held retry to release. Existing coverage calls retry_due_workers directly without queue_event_id, so 9 focused schema tests pass without covering this path. Preserve task-quarantine-held retries through poll/assignment (and prevent their stale failure reclassification), then add a poll_workers integration regression with queue_event_id proving no launch before reopen and exactly one launch after governed reopen.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

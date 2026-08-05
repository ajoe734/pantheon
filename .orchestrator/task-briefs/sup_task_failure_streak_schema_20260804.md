# Task Brief: SUP-TASK-FAILURE-STREAK-SCHEMA-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a failure_streak counter and quarantined status to the task schema
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review rejected at 1d90f933d2c66022b82facb847c67e027e7d4a8f: full supervisor regression suite fails (591 passed, 4 failed). Update the four PollWorkersRecoveryTests that still assert clear_task_failure_streak after production paths now call clear_task_failure_streak_after_worker_completion (or retain the legacy call only if semantically required), then rerun PANTHEON_PY=$(python3 scripts/dev/provision_python_distribution.py --print-python) && $PANTHEON_PY -m pytest -q .orchestrator/test_supervisor.py. Preserve the queue-backed retry-hold regression and exact-head evidence binding.

## Summary
Makes repeated dispatch failure visible on the board itself instead of only in raw activity-log JSONL, closing the exact gap that made SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 indistinguishable from an untouched task after 5 failed attempts.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

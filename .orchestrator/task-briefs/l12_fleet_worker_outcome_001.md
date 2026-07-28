# Task Brief: L12-FLEET-WORKER-OUTCOME-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Claude-priority closeout: orphaned worktree condition was resolved by supervisor cleanup; do not restart implementation. Close out merged PR #4279 exact head 3c5f1a2774263f920f02032358b07b84717c7ce5 / merge 6c57f19932d84903ec6bea700205f4a87229f59c, then handoff to Codex.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review failed on a boot-crash edge: with an existing status_activity_outbox and retry budget exhausted, _prepare_missing_worker_terminal_outcome_locked returns None; the worker/queue become failed but the task remains in_progress with zero blocker, so it still pretends to progress. Reproduced independently: worker_status=failed, task_status=in_progress, blocker_count=0, outbox_still_pending=true. Recover or safely compose the pending outbox before atomically persisting the missing-worker terminal outcome, and add a regression test for this case; retain task/run/provider/reason evidence. PR #4279 head 3c5f1a2774263f920f02032358b07b84717c7ce5 and merge 6c57f19932d84903ec6bea700205f4a87229f59c were otherwise verified; 14 RuntimeLeaseReconciliationTests, py_compile, diff --check, Branch CI Gate and Orchestrator Sync passed.

## Summary
Make missing worker processes become bounded retry/reopen outcomes

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

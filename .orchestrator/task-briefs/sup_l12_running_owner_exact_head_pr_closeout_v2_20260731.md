# Task Brief: SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Supersede Wave0X #4396 governed closeout with current-head spec
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Helper-claimed by Codex while Codex2 is dispatch-paused previous owner Codex2 becomes reviewer.

## Summary
Supersedes preempted immutable task SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731 after bridge rejected spec update. #4396 is no longer draft but still blocked by merge/root-freeze closeout.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

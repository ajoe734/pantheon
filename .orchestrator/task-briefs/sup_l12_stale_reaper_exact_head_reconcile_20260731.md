# Task Brief: SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile stale failure-streak reaper exact PR head before Wave 0 closeout
- Status: ready_for_independent_review
- Owner: Codex2
- Reviewer: Antigravity
- Next: Antigravity independently reviews the task-scoped reconcile evidence; current PR #4385 head f5e70e86 is not approval-eligible because its manifest names a nonexistent anchor SHA.

## Summary
PR #4385 current head differs from the reviewed task row head; reconcile exact-head proof before treating stale failure-streak reaper as a Wave 0 dependency.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

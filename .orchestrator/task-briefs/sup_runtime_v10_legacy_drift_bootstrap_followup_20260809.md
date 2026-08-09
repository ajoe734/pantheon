# Task Brief: SUP-RUNTIME-V10-LEGACY-DRIFT-BOOTSTRAP-FOLLOWUP-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Design governed bootstrap for accumulated legacy context drift
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Claude2
- Next: Human/Ops invalidated PR #4667 df7e21918 approval as factually false: code archives mutable HEAD and the race test never performs A->B->A. Owner must bind archive/render to captured expected_head/object, add deterministic ref-switch coverage, refresh evidence/head, then obtain independent exact-head review.

## Summary
After PR #4648 merged, the governed f5570754 retry still failed before mutation because the legacy mutable incumbent already contains four supervisor-generated task-brief overwrites. Implement only the source transaction boundary needed to bootstrap that accumulated historical context drift without weakening general tracked-tree cleanliness or touching live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

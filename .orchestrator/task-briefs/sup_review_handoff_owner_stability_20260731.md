# Task Brief: SUP-REVIEW-HANDOFF-OWNER-STABILITY-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Preserve owner identity across review handoff and closeout
- Status: todo
- Owner: Codex2
- Reviewer: Antigravity
- Next: Helper-claimed by idle Codex2; previous owner Antigravity becomes reviewer.

## Summary
Repair the supervisor failure-loop/closeout boundary so a successful review handoff does not look like a missing outcome and immutable delivery identity remains valid through governed reassignment.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: SUP-REVIEW-IDENTITY-BOUND-REASSIGN-GUARD-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Guard bound review identity from auto reassignment
- Status: todo
- Owner: Codex
- Reviewer: Claude2
- Next: Helper-claimed by Codex while Claude2 is dispatch-paused.

## Summary
Fail closed when an exact-head review binding would lose its designated independent reviewer; reject same-account reviewer pairs.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

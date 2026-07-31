# Task Brief: SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Extend the program-specific guarded dispatcher for current-proof remediation
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Helper-claimed by Codex while Codex2 is dispatch-paused.

## Summary
Bootstrap the existing L12 program-specific dispatcher so the true supervisor can safely fan out the newly audited 28-task remediation DAG to auto workers. This bootstrap is dependency-gated on the scheduler runtime repair and is the only task sent through the generic bridge.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

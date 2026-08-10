# Task Brief: SUP-ASSISTANT-DEV-BRIDGE-RECOVERY-REARM-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Rearm exact recovery after a failed recovered-packet drain
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Assignment created

## Summary
Repair the narrow retry gap where an exact failed packet was recovered successfully, its governed drain failed before assignment, and the packet returned to failed storage while its durable recovery record remained queued. Add a fail-closed supported rearm path without permitting manual queue or recovery-record edits.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

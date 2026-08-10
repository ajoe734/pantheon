# Task Brief: SUP-RUNTIME-V10-ROLLBACK-COLLISION-SAFE-DESTINATION-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make rollback materialization collision-safe without root reuse
- Status: todo
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Assignment created

## Summary
Repair the next narrow rollback-materialization collision discovered after PR #4726: keep the existing different-root 0305 runtime untouched and provide a deterministic, independently verified fresh rollback identity for the governed promotion transaction.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

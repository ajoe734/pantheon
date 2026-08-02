# Task Brief: SUP-SCHEDULER-FIXED-CADENCE-BATCH-MUTATIONS-OPERATOR-V9-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make supervisor cadence deadline-based and batch canonical/runtime mutations
- Status: todo
- Owner: Codex2
- Reviewer: Human/Ops
- Next: Assignment created

## Summary
Remove full-sleep drift and per-task mutation convoys so dispatch cadence tracks its deadline while preserving fail-closed governance.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

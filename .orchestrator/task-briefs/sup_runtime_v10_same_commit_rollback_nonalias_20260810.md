# Task Brief: SUP-RUNTIME-V10-SAME-COMMIT-ROLLBACK-NONALIAS-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair same-SHA rollback identity materialization for supervisor promotion
- Status: todo
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Assignment created

## Summary
Source-only repair for the exact rollback materialization collision discovered by the governed alias-guard live-promotion preflight. Keep the live runtime untouched and make the candidate/rollback identity contract explicit and independently testable.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

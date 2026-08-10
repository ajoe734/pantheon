# Task Brief: SUP-RUNTIME-PROMOTION-EXISTING-ROLLBACK-ROOT-20260810

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promotion bootstrap when rollback root already exists
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Supervisor recorded worker failure streak 1/2 for SUP-RUNTIME-PROMOTION-EXISTING-ROLLBACK-ROOT-20260810.

## Summary
A controlled promotion of dev merge f8c212d5 aborts before changing config because the legacy mutable incumbent already occupies command-runtimes/5877...; the rollback materializer requires a fresh destination. Repair only this exact same-root bootstrap case, preserving all integrity checks and promotion gates.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

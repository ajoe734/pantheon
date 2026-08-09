# Task Brief: SUP-RUNTIME-V10-LEGACY-CONTEXT-CANONICALITY-FOLLOWUP-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind historical generated task-brief canonicality for legacy bootstrap
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Fix head 0b82f8991 before review: add task_id and exact task/path/event/source/boundary association; event swap and another-valid-ancestor source/boundary must fail; missing commit objects fail closed; production bindings only 1364/21a8c81a and 2132/40bb9032; remove fixture-only 634 and unrelated 3092; use exact live fleet bytes from PANTHEON_COMMAND_ROOT so the fixture hashes to 40bb9032, not 255c0d0e; correct evidence and request fresh review.

## Summary
PR #4667 retained strict byte equality by re-rendering legacy brief content from current bound config, but the active pre-fix brief now fails that comparison before any promotion mutation. Repair only a fail-closed historical-context provenance boundary for the known generated residue; do not broaden drift acceptance or touch the live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

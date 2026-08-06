# Task Brief: LIFECYCLE-PROJ-BFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Serve Trade Journey and loop-run reads from Postgres
- Status: todo
- Owner: Antigravity
- Reviewer: Claude
- Next: Codex quota exhausted 2026-08-05: reassigned Codex2->Antigravity / Codex->Claude to keep fleet moving

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-BFF-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

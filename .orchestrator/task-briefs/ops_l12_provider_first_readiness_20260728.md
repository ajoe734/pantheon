# Task Brief: OPS-L12-PROVIDER-FIRST-READINESS-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify Claude/Antigravity provider-first readiness
- Status: todo
- Owner: Codex2
- Reviewer: Codex
- Next: Materialized from PR #4288 execution graph. Scope: prove whether Claude/Antigravity supervisor provider lanes are actually healthy and dispatchable; if not healthy, record blocker and keep assigning to available real lanes without falsely claiming provider-first execution. Do not edit .orchestrator/config.json.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

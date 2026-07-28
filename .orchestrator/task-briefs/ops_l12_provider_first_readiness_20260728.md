# Task Brief: OPS-L12-PROVIDER-FIRST-READINESS-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify Claude/Antigravity provider-first readiness
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Codex refreshed the provider-first evidence against command runtime `061408b09aa06943813c97334054bfa29b79e236`; publish the exact task head through PR #4293 and request independent Claude review.

## Summary
- Prove whether the live Claude and Antigravity supervisor lanes are healthy and dispatchable without editing `.orchestrator/config.json`.
- If a lane is unhealthy, record the fail-closed result and prove that healthy real lanes continue draining work instead of claiming provider-first success.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

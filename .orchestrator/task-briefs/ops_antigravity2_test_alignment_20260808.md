# Task Brief: OPS-ANTIGRAVITY2-TEST-ALIGNMENT-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix tests broken by antigravity2 dispatcher enable
- Status: in_progress
- Owner: Codex2
- Reviewer: Antigravity
- Next: Human/Ops authorized governed recovery: resume source-only #4624 exact-head review repair. Codex2 must bind a committed evidence manifest to the current PR head, preserve the approved AG2 enablement policy, and obtain an Antigravity independent exact-head review before merge. Do not alter live runtime, fleet routing, or unrelated configuration.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

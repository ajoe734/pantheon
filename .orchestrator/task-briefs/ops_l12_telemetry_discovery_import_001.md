# Task Brief: OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Eliminate telemetry unittest discovery loader errors
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Chair reassigned owner from Claude to Codex: The task remained blocked after Codex was temporarily auth-paused; that pause expired at 2026-07-27T18:29:28Z, Codex GitHub access is healthy, the task is a bounded rerunnable test/evidence repair rather than a human gate, and Codex is distinct from reviewer Codex2. Task returned to todo for a blocked-owner rescue dispatch.

## Summary
修正 telemetry 完整 unittest discovery 的兩個裸模組 import error，讓乾淨 repo-root 與 package discovery 都能零 loader error。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

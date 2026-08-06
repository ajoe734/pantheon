# Task Brief: SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate fleet bootstrap on exact runtime root coherence
- Status: todo
- Owner: Antigravity
- Reviewer: Claude
- Next: Codex quota exhausted 2026-08-05: reassigned Codex2->Antigravity / Codex->Claude to keep fleet moving

## Summary
在任何 L12 重盤點或 25-task admission 前，證明 supervisor、watchdog、worker runner、command runtime、Git source root 與 status root 各自綁定正確且真實 auto-worker 可持續運行。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

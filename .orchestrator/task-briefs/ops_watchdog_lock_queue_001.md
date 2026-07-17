# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: todo
- Owner: Antigravity
- Reviewer: Codex
- Next: [auto-reap] in_progress 34m with no live worker; run abandoned. Reset to todo for re-dispatch (auto-reap #1/2).

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

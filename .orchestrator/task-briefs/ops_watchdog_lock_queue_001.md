# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED on exact head 97d1cca10: revert/split out-of-scope 41d510538 and restore fail-closed archive conflict checks (exact-head scripts/test_ai_status.py: 1 failed, 74 passed); narrow EAGAIN handling to admission-lock acquisition, enforce a hard-bounded contention path, and explicitly report every dropped metric with body-EAGAIN/metric-error/dry-run/owner-crash/exact-once tests; replace the stale/manual evidence with exact installed-SHA proof and three consecutive real scheduler cycles with per-cycle healthy readback/process/hash evidence. Merged PR #3798 still uses manual lock-window runs, one final health read, and unapproved live rotation-file moves, so it does not satisfy acceptance. Resubmit a new exact head; no approval.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

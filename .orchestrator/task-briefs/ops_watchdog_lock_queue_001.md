# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED after exact-head review of merged PR #3831 / c5592c1. Code gate passes: 43 watchdog tests, 90 common tests plus 52 subtests, and 12+12 isolated probes; current live health is true. P1 evidence gate fails: the one-minute cron and raw logs show skip/lock_contention at 01:33-01:36 and 01:38-01:39 between the claimed healthy 01:32/01:37/01:40 samples, so these are not consecutive scheduler cycles and contradict the no-contention claim. Capture a genuinely consecutive three-tick window with the live config, per-tick require-watchdog health JSON, exact dev-root/deployed SHA, watchdog and waiter process counts, and timestamped state/metric/config hashes; do not kill processes or delete locks. P2 artifact corrections: postmerge evidence has only Cycle 3 health and final-only hashes/no counts; its Generated timestamp predates the samples; dev-root was 5fdf28f during 01:32-01:40 rather than exact HEAD 4d7388; primary evidence retroactively labels 16:37-16:39 samples as 4d7388 although that merge was created at 23:02Z. Preserve exact historical SHA/time truth, archive raw fixture metadata, and update status/conclusion before re-review.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

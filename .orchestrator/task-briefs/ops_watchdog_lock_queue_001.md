# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Completed and resolved review changes. Unit tests run successfully (44 passed). Added initially-free concurrent probe test test_initially_free_concurrent_probes_max_one_owner. Fixed hard-coded restart_count_window/hour=0 on contention to None. Historical parent commit corrected to 43033aa40. Captured natural three-tick evidence manifest on c5592c1068a8570c659cb484dbd53466c080769b (02:26-02:28Z), including health check JSONs, process status, file stat/hashes, and reflog proof, archived in archive/ops_watchdog_three_tick_manifest.json. Composed task branch and prepared for final closeout/PR merge.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

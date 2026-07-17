# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED on merged PR #3815 / merge 288fb046e. P1 .orchestrator/supervisor_watchdog.py:749-755 returns from the try before the else, so normal success calls __exit__ zero times (reproduced entered=1/exited=0) and relies on CPython destruction for lock release; explicitly release on success and add a normal-path exact-once test. P1 lines 742-745 silently swallow non-EAGAIN contention-metric failures (reproduced EACCES => stderr empty/no result marker); surface every drop/error and add wrapper-level coverage. P2 lines 713-718 classify any EAGAIN from the whole __enter__ path, including validation I/O, as lock contention; distinguish actual flock contention from I/O/config failures. Acceptance evidence is invalid/stale: documented SHA 710958642a8... does not exist, cycles 16:37-16:39 predate implementation 959dab at 16:42 and PR merges, dev-root remains 6d833 and differs from #3815, and current --require-watchdog is unhealthy. Deploy a descendant containing 288fb, then capture three consecutive real cycles with exact SHA and per-cycle health/process/hash proof. Validation: watchdog 37 passed; ai-status 74 passed + 15 subtests, but these P1 paths remain uncovered. Resubmit a fresh exact head.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

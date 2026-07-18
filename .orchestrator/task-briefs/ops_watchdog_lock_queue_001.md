# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED on exact deployed 7be97fa5cffe99e704e672b5aa56abe1515da5d9 / PR #3822. P1 live acceptance: at 2026-07-17T22:37:55Z, supervisor_runtime_health.py with the live config and --require-watchdog reports healthy=false; watchdog state is 2026-07-17T22:23:01Z (age 894.6s > 180s) while 22:26-22:37 are consecutive bounded skip/lock_contention ticks. No watchdog waiter accumulation was observed, but freshness/restoration and sustained three-cycle health are not met. Do not kill processes or delete locks; capture a new exact-SHA window after normal probes resume with per-cycle health JSON, process/waiter counts, and timestamped state/metric hashes. P2 evidence: durable repo evidence still pins stale 98aa5611a/42 tests; the external 122-line artifact has only one health JSON and lacks per-cycle counts/hashes/test results. P2 code: .orchestrator/common.py:235 and supervisor_watchdog.py:398 classify any BlockingIOError (reproduced EINPROGRESS) as contention; restrict conversion/drop to errno EAGAIN/EWOULDBLOCK. P2 regression: test_common.py:653 raises on the pre-flock identity check, so it never tests post-flock EAGAIN; let the first identity check pass, fail the second, and assert flock occurred. Reviewer validation: 43 watchdog tests passed; 81 common tests + 50 subtests passed.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED after exact-head re-review of merged PR #3835. PASS: c956 runtime paths are unchanged; exact-c956 isolated fixture rerun has 12+12 terminal skip/lock_contention probes in 0.492s and 0.402s, null sample restart counters, healthy post-release state/metric/activity; live require-watchdog is healthy; 281 supervisor tests passed, the combined suite had 216 passed plus one inherited auto-worker-env failure that passed when rerun isolated. P1 implementation: the contention path has no enforced end-to-end deadline; before returning JSON it synchronously reads diagnostics and appends the contention metric with os.write plus file and directory fsync, so a free metric lock with stalled storage can still leave cron competitors unbounded. Add a fault-injected deadline test and bound or decouple this durable write without losing aggregable evidence. P1 evidence: manifest proves a 02:59:02 skip/lock_contention append between 02:58:02 and 02:59:35, contradicting the document claim of no skips/contentions and its static f3bb contention hash; metric sizes and health snapshots also disagree between doc and manifest. Archive and explain the exact bounded contention event or capture a fresh three-tick window, correct all hashes/sizes/snapshots, and include explicit per-tick holder/waiter provenance. P1 redaction: ticks process arrays contain 92,929 chars including 35 unrelated full worker prompts/argv plus host/container identifiers; replace with minimal PID/executable/scoped evidence. P2 fixture: assert repo_head equals intended SHA, use one absolute batch deadline, and assert all 12+12 restart counters are null plus exactly-once post-release writes. Do not kill processes or delete locks. Resubmit for review.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

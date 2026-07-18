# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED after exact-head review of PR #3832 / merge c9560db5c. PASS: Branch CI/Orchestrator Sync; 44 watchdog tests; 134 watchdog+common tests plus 52 subtests; 217 watchdog+common+ai-status tests plus 75 subtests; 281 supervisor tests; exact-head isolated 12+12 probes terminal with null contention counters; live c956 health=true and null counters observed. P1 evidence gate remains open: archived postmerge_lock_contention_fixture.json was generated on c5592c106 and still records restart counters 0, contradicting the final c956 None contract; rerun and archive the fixture on the exact deployed final head. The 02:26-02:28 c559 metrics are genuine consecutive healthy ticks and their append-only prefix hashes/reflog verify, but ops_watchdog_three_tick_manifest.json process_evidence stores only supervisor pid/cmdline/thread count, not the claimed per-tick watchdog process enumeration or runtime-admission holder/waiter count. On the exact deployed final SHA, capture a new natural three-tick window with raw timestamped watchdog process/count and lock holder/waiter outputs, per-tick live-config require-watchdog JSON, state/metric/config stat+hash, rev-parse/reflog proof; reconcile docs and mark old c559 artifacts historical. Do not kill processes or delete locks. Resubmit for review.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

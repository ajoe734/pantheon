# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED on exact head 4d1dabe03 / PR #3821. P1 deploy/evidence: installed PANTHEON_COMMAND_RUNTIME_SHA 98aa5611a is not a descendant of final code fix 2af09a9fb (merge-base check fails; installed files lack LockContentionError/with-lock fix). The 20:29:02, 20:30:02, 20:31:02 cycles therefore exercised old code, and cycle 1 predates fix commit 20:29:54. Deploy a descendant containing the final fix (and the next corrective commit), then capture 3 consecutive real scheduler cycles with exact deployed SHA plus per-cycle require-watchdog health, supervisor/watchdog process and waiter counts, and timestamped hashes. P2 .orchestrator/common.py:231-245 catches EAGAIN from both flock and post-flock identity validation; reproduced flock success followed by _assert_stable_lock_identity EAGAIN becoming LockContentionError/benign skip. Convert only EAGAIN/EWOULDBLOCK thrown by the flock syscall, add helper-level regression, and ensure run_watchdog catches acquisition contention only rather than a LockContentionError from the locked body. Evidence also omits cycle-1 health and per-cycle process/hash proof; fixture still labels implementation_merge=3705570e. Current deployed 98aa require-watchdog was unhealthy at 2026-07-17T21:17Z (probe stale after ongoing bounded skips). Validation run: watchdog 42 passed; common 80 passed + 50 subtests; isolated 12+12 probes passed, but none covers the reproduced validation-EAGAIN path.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

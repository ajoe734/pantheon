# Task Brief: OPS-WATCHDOG-LOCK-QUEUE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor watchdog lock contention (capacity rebalance to Antigravity)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: CHANGES REQUIRED after exact-head review of merged PRs #3823/#3824. Code gate passes: 43 watchdog tests; 81 common tests + 50 subtests; 12+12 isolated probes; b330 is in deployed merge 4d7388. P1 live evidence is invalid: cron uses /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json, but the saved 23:09 health JSON used dev-root default state (PID 2584782) and reports healthy=false; live-config read-only check at 2026-07-18T01:25:02Z sees actual PID 3670380 healthy but watchdog age 421s >180, so overall healthy=false while normal probe is contended. Wait for natural lock release (no kill/delete), then capture 3 consecutive real scheduler cycles using --repo /home/lupin/pantheon-ci-deploy/dev-root --config-path /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json --require-watchdog --json, with per-cycle health, exact deployed SHA, process/waiter counts, state/metric timestamps and hashes. P2 artifact corrections: do not label healthy=false as healthy; reconcile stale 98aa/710958/6d833/3705570 metadata; describe the actual manual acquisition-only LockContentionError boundary (code is not a with block); make fixture exact-head metadata current; use Reviewer: Codex on the corrective task commit.

## Summary
容量調度：由 Antigravity 執行 watchdog lock contention 修復，Codex 做獨立驗收；交付 bounded single-flight 行為、測試、部署與三個排程週期證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

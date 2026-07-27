# Task Brief: SUP-TASK-STATE-LOCK-LATENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor task-state lock latency and projection truth
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 independent review approved: PRs #4263/#4264 merged to dev; candidate 2abc735b full suite 685 passed plus 82 subtests; fresh governed 2050-event 134.852 MiB benchmark completed 8/8 commands across 4 workers and 16 run_once cycles at 1.840s p95/max with exact event-2066 projection; required checks and lock/projection/termination boundaries verified.

## Summary
縮短 supervisor task-state/runtime-admission 鎖持有時間，修正 caught_up 語意，讓 approve/assign 與 heartbeat 不再被數分鐘 projection 阻塞。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

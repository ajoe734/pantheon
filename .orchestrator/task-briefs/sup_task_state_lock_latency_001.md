# Task Brief: SUP-TASK-STATE-LOCK-LATENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor task-state lock latency and projection truth
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: The PR #4239/#4250/#4253 review failures are remediated on candidate `611b2a480`: archive and ownerless-PR network work execute outside runtime admission with freshness gates; termination fails closed without start ticks and remains nonterminal until post-lock confirmation; a real 2050-event / 134.852 MiB benchmark ran eight governed approve/assign/note/reopen commands across four workers during ten full `run_once` cycles at 1.662s p95 with exact event-2066 projection. Full related validation passed (680 tests, 82 subtests). Open the remediation PR, pass checks, and hand off to Codex2 for fresh independent review.

## Summary
縮短 supervisor task-state/runtime-admission 鎖持有時間，修正 caught_up 語意，讓 approve/assign 與 heartbeat 不再被數分鐘 projection 阻塞。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

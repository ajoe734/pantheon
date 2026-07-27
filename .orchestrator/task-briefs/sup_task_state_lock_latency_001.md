# Task Brief: SUP-TASK-STATE-LOCK-LATENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor task-state lock latency and projection truth
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Remediation PR #4263 merged to dev at `52aa8a623e68336e1965d7241950cb3c22f0c827` with Commit trailers, Runtime mirror guard, Python packaging provision, and Smoke acceptance green. Final executable candidate `2abc735b0` passed the real 2050-event / 134.852 MiB benchmark with eight governed approve/assign/note/reopen commands across four workers during seventeen full `run_once` cycles at 1.296s p95/max and exact event-2066 projection; full related validation passed (685 tests, 82 subtests). Land this delivery-evidence update, then hand off to Codex2 for fresh independent review.

## Summary
縮短 supervisor task-state/runtime-admission 鎖持有時間，修正 caught_up 語意，讓 approve/assign 與 heartbeat 不再被數分鐘 projection 阻塞。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

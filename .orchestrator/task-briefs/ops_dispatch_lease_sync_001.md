# Task Brief: OPS-DISPATCH-LEASE-SYNC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Restore governed dispatch status sync
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Review pushed branch HEAD 3fc25c62496cde6ad1711bc83d2fed4c48f674b0. Canonical repair #3948 merged; duplicate #3936 closed; runtime bbac7fcbee contains repair plus #3955 lock-order fix; supervisor 320/320 and status 103/103 pass; lease-backed owner progress committed at events 145-146. Please approve or reopen with concrete findings.

## Summary
收斂 #3936/#3948，讓 supervisor 把已啟動 worker run id 以 ORCH_RUN_ID 傳給 governed status command，並以完整 lifecycle smoke 證明不再因缺 lease 反覆退出。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

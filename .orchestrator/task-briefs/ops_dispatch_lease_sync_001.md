# Task Brief: OPS-DISPATCH-LEASE-SYNC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Restore governed dispatch status sync
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Recovered authoritative task state from validated event 138 after isolated lock test polluted event 139 with LOCK-ONE/LOCK-TWO; recovery appended as a new journal commit and parity verified. Resume governed lifecycle smoke on merged runtime bbac7fcbee827b916e565e806eacfbec18a1dac6.

## Summary
收斂 #3936/#3948，讓 supervisor 把已啟動 worker run id 以 ORCH_RUN_ID 傳給 governed status command，並以完整 lifecycle smoke 證明不再因缺 lease 反覆退出。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

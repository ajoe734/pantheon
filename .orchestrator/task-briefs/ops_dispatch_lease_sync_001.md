# Task Brief: OPS-DISPATCH-LEASE-SYNC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Restore governed dispatch status sync
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved with supervisor 320/320, status 103/103, and focused lease 6/6. Evidence/tests PR #3956 merged to dev as 46638a7e7e5e9b81afcc2e20c09d124bdaa9f550; owner closeout may now record done.

## Summary
收斂 #3936/#3948，讓 supervisor 把已啟動 worker run id 以 ORCH_RUN_ID 傳給 governed status command，並以完整 lifecycle smoke 證明不再因缺 lease 反覆退出。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

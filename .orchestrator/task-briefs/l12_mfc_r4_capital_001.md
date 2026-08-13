# Task Brief: L12-MFC-R4-CAPITAL-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Execute the active RuntimeBinding artifact in the default paper signal producer
- Owner: Antigravity
- Reviewer: Antigravity2
- Status: todo
- Next: Read the merged execution catalog and GAP/SD, work only in declared scope, start from latest remote dev in a clean worktree, deliver PR/checks/review/merge/readback; do not create repair tasks from E2E failures.

## Summary
default paper decision 執行 pinned artifact；固定 AAPL BUY 只留 explicit smoke。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: L12-MFC-R4-SOURCE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make the existing source controller the durable scheduled owner
- Owner: Antigravity
- Reviewer: Antigravity2
- Status: in_progress
- Next: Supervisor auto-started L12-MFC-R4-SOURCE-001 after successful dispatch.

## Summary
正式長駐 source controller 與 bounded smoke 分離；不建立第二 scheduler。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

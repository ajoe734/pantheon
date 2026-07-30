# Task Brief: SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable stale L12 failure-streak reaper
- Status: ready_for_independent_review
- Owner: Codex
- Reviewer: Antigravity
- Next: Antigravity reviews PR #4385 at the exact rebased head before merge.

## Summary
把 11:33Z 清 stale missing_process streak 的 live repair 正式做成 supervisor policy 與 regression，避免 Claude2/Antigravity L12 dispatch 再被舊 failure loop 卡死。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

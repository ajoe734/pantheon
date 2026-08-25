# Task Brief: SUP-DEV-RESOURCE-ADMISSION-20260825

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add pre-dispatch admission for the shared Pantheon dev environment
- Owner: Antigravity
- Reviewer: Codex
- Status: todo
- Next: Implement the shared-dev pre-dispatch resource admission first; preserve the existing environment lease as the sole execution lock; add positive/negative tests and deliver the tooling change to dev.

## Summary
讓只有 hosted/release task 能宣告 pantheon-dev execution resource，supervisor 在 worker 啟動前以容量 1 排程；功能 task 繼續在隔離 worktree 最大平行執行。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

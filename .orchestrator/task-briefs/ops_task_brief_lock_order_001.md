# Task Brief: OPS-TASK-BRIEF-LOCK-ORDER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix nested task-state lock during worker brief generation
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Supervisor auto-started OPS-TASK-BRIEF-LOCK-ORDER-001 after successful dispatch.

## Summary
修復 supervisor 在已持有 runtime_admission/task_state 鎖時再次取得 task_state，造成完整 task brief 生成失敗並退回 minimal context。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

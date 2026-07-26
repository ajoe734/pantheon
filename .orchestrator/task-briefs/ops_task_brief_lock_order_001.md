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

## Implementation Evidence
- `write_task_brief` now reads the active task and archived dependencies under the configured canonical task-state lock, with `TaskResolver` bound to that same status root.
- The run_once/process_queue regression trace records exactly one real `task_state` acquisition after `runtime_admission`; full task fields, archived dependency status, and artifacts render without the minimal-context fallback.
- Verified with `PYTHONPATH=.orchestrator python3 -m unittest test_supervisor` (334 tests), clean-env `test_common` (90 tests), and focused post-merge regression execution.

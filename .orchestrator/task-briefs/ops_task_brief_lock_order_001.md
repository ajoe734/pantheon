# Task Brief: OPS-TASK-BRIEF-LOCK-ORDER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix nested task-state lock during worker brief generation
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 independent review passed: PR #4197 merged at e82371783e18c4bac7b0c2ca650c0904a8c004f3; remote dev tip 403e30bd985ea9b0c166180103a0ab64e4e35d4f includes implementation and PR #4202 proof a77558b610effd5af1fae63605c34ed3e1bd01b5. Diff preserves runtime_admission -> task_state -> activity_audit ranks and fail-closed guard, binds archive resolution to the configured status root under one re-entrant task_state acquisition, and renders status/owner/reviewer/next/archive dependency/artifacts without minimal fallback. Independent clean-env validation passed: focused regression 1/1, test_supervisor 334/334, test_common 90/90.

## Summary
修復 supervisor 在已持有 runtime_admission/task_state 鎖時再次取得 task_state，造成完整 task brief 生成失敗並退回 minimal context。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

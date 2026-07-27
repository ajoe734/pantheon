# Task Brief: SUP-TASK-STATE-LOCK-LATENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bound supervisor task-state lock latency and projection truth
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Root continuation: second-pass supervisor/worker repair for missing PANTHEON_WORKTREE_ROOT/ORCH_WORKSPACE_PATH dispatch-sync binding and stale origin/dev refspec. Continue in a clean task worktree; no live deploy or manual worker kill.

## Summary
縮短 supervisor task-state/runtime-admission 鎖持有時間，修正 caught_up 語意，讓 approve/assign 與 heartbeat 不再被數分鐘 projection 阻塞。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

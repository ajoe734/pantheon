# Task Brief: SUP-COMMAND-RUNTIME-REFRESH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Refresh governed supervisor command runtime without config changes
- Status: todo
- Owner: Claude
- Reviewer: Codex2
- Next: Wait for SUP-WORKER-TRUTH-RECONCILE-001 to merge; then supervisor dispatches Claude for serialized runtime refresh and Codex2 review.

## Summary
在 supervisor truth 修復合併後，將 governed command runtime 更新到精確 accepted dev；重用既有 config，不改 config，不中斷 active lease，保留 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

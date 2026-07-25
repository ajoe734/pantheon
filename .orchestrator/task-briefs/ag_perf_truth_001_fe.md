# Task Brief: AG-PERF-TRUTH-001-FE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Remove simulated Strategy Performance product data
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Review approved: PR #502 and exact FE/BFF accepted deployment verified; Codex2 should perform task-closeout finalization and mark done.

## Summary
移除 getSimulatedDetails 與 local-only success；依 BFF availability/provenance 顯示真實/unknown 狀態，suggestion action 只在 receipt readback 後成功。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

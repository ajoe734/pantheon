# Task Brief: SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 fleet runtime reliability readback
- Status: in_progress
- Owner: Codex
- Reviewer: Antigravity
- Next: Revalidating the approved readback and preparing the existing task PR for fresh Antigravity review after the owner/reviewer reassignment.

## Summary
盤點 supervisor/auto-worker runtime，記錄 Antigravity/Claude2/Codex/Codex2 實際可用性與失敗循環，不改 config。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

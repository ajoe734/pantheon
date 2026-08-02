# Task Brief: SUP-ASSIGNMENT-FALLBACK-ATOMIC-REBINDS-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Atomically rebind owner and reviewer across fallback dead ends
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Assignment created

## Summary
讓 unavailable owner 的 fallback 同時計算合法 owner/reviewer pair，避免唯一 fallback 已是 reviewer 時整批卡死。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

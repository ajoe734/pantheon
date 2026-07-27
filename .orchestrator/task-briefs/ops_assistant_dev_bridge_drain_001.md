# Task Brief: OPS-ASSISTANT-DEV-BRIDGE-DRAIN-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair supervisor DevTaskPacket drain and bridge command binding
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Assignment created

## Summary
修正 DevTaskPacket bridge 在 authoritative task-state 下使用 status-root script，導致 assignment 沒寫入 task-state event log 而被 projection 沖掉；保留 actor/lease與pydantic auto-drain可重現證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

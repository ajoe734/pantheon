# Task Brief: SUP-WORKER-FAILURE-ENVELOPE-GATE-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate worker failures on authoritative terminal envelopes
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Assignment created

## Summary
停止從任意 transcript 與原始碼片段誤判 provider quota；只消費 runner/provider 的權威終止證據。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

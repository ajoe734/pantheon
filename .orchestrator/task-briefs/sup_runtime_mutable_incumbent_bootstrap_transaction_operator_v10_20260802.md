# Task Brief: SUP-RUNTIME-MUTABLE-INCUMBENT-BOOTSTRAP-TRANSACTION-OPERATOR-V10-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make config switch and mutable-incumbent bootstrap one atomic promotion transaction
- Status: todo
- Owner: Codex2
- Reviewer: Human/Ops
- Next: Implement source-only atomic bootstrap repair from PR #4522 NO-GO evidence; no live signal or config mutation in this source task.

## Summary
把 live config entrypoint 改寫納入同一個 rollback-safe promotion transaction，安全地把目前 mutable dev-root incumbent bootstrap 到第一個 immutable command runtime。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

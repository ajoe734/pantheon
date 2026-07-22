# Task Brief: AG-WS-OPS-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable Workshop versions and selection
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Helper-claimed by idle Codex2; previous owner Claude becomes reviewer.

## Summary
實作 workshop versions list/create/select 三條 deferred API，含 durable StrategySpec version、lineage、idempotency、ETag CAS、tenant isolation 與 restart persistence。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: AG-WS-OPS-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governed Workshop research consultation and conclusion
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Supervisor auto-started AG-WS-OPS-002 after successful dispatch.

## Summary
實作 research-runs、consultations、conclude 三條 deferred API，綁定 durable workshop version、真實 downstream lineage、idempotency 與 atomic terminal transition。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

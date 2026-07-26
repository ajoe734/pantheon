# Task Brief: L12-ALPHA-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dispatch approved Alpha replication into authoritative ExperimentRuns
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Replace stub handoff with governed authoritative research execution

## Summary
統一 StrategySpec identifier，限制 approved review 才能進 queue，補 tenant、lease、DLQ/replay，將結果寫入真實 research authority 而非 stub/local run。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

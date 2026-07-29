# Task Brief: L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest auth volume applicability matrix workstream
- Status: todo
- Owner: Codex
- Reviewer: Antigravity
- Next: Workstream under L12-MANIFEST-001. Do not edit .orchestrator/config.json. Add or propose integrable readback/validator/evidence changes for per-worker auth and durable-volume applicability.

## Summary
建立每個 worker 的 auth 與 durable-volume applicability matrix，補 readback/validator 缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

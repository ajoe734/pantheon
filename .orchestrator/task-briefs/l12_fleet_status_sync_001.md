# Task Brief: L12-FLEET-STATUS-SYNC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Claude-priority closeout: merged PR #4282 exact head e806affaa279f8b9d4b41bae6117a9431c99b90e / merge a0020c5ac50e510467a5e80c412c7703245cf4dd exists; create task-scoped closeout evidence and handoff to Codex. Do not restart implementation.
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by Codex while Claude is dispatch-paused.

## Summary
Stop stale supervisor status/source_ref regressions

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

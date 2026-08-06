# Task Brief: SUP-PROVIDER-PROBE-HYSTERESIS-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Decouple provider capability probing from the dispatch hot path and add failure hysteresis
- Status: todo
- Owner: Antigravity
- Reviewer: Claude
- Next: Codex quota exhausted 2026-08-05: reassigned Codex2->Antigravity / Codex->Claude to keep fleet moving

## Summary
The single highest-value fix from the 2026-08-04 session: a flaky, auto-refreshing, inline capability probe silently zeroed out dispatch for Claude/Claude2/Antigravity for hours because one transient CLI timeout under load was treated as ground truth with no debounce and no visible signal.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

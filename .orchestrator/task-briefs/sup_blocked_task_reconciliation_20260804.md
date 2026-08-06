# Task Brief: SUP-BLOCKED-TASK-RECONCILIATION-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add structured, checkable blockers and a generic blocked-task reconciliation pass
- Status: todo
- Owner: Antigravity
- Reviewer: Claude
- Next: Codex quota exhausted 2026-08-05: reassigned Codex2->Antigravity / Codex->Claude to keep fleet moving

## Summary
Root-cause fix for a pattern hit four separate times live on 2026-08-04: blocked is a one-way door in dispatch_ready_tasks (owned/review/finalize status lists never include it), so a task whose blocking condition later resolves (CI turns green, a dependency finishes) stays frozen until a human manually reopens it. The codebase already has five prior one-off 'reaper' tasks attempting fragments of this same fix, and all five are themselves stuck blocked as of today -- proof point patches don't compose. This task builds the one generic mechanism instead and must be able to retire all five as superseded.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

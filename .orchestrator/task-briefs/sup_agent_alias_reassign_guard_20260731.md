# Task Brief: SUP-AGENT-ALIAS-REASSIGN-GUARD-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Keep supervisor reassignments on canonical agent identities
- Status: in_progress
- Owner: Antigravity
- Reviewer: Human/Ops
- Next: Review rejected: helper-level filtering alone does not satisfy the acceptance contract. Add end-to-end regressions for maybe_reassign_task_after_worker_failure and normalize_mainline_task_assignment proving the exact Copilot (legacy alias) incident cannot persist that label into owner/reviewer; prove resulting task remains usable by governed status commands and that handoff/blocker identities remain canonical. Keep the narrow no-config-change implementation if those path-level tests prove it; otherwise repair the canonicalization boundary. Re-run focused plus full supervisor and ai_status suites, then hand off again.

## Summary
修正 supervisor fallback reassignment 使用 display_name 而非 canonical agent identity 的缺陷，避免 Copilot legacy label 汙染 authoritative task state。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: SUP-BLOCKED-TASK-RECONCILIATION-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add structured, checkable blockers and a generic blocked-task reconciliation pass
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: PR #4587 merged into dev at feb46f745afea8b5cccab972e3c3cc53c6f50176: docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md now exists on dev. The design-doc-missing blocker no longer applies -- retry from scratch.

## Summary
Root-cause fix for a pattern hit four separate times live on 2026-08-04: blocked is a one-way door in dispatch_ready_tasks (owned/review/finalize status lists never include it), so a task whose blocking condition later resolves (CI turns green, a dependency finishes) stays frozen until a human manually reopens it. The codebase already has five prior one-off 'reaper' tasks attempting fragments of this same fix, and all five are themselves stuck blocked as of today -- proof point patches don't compose. This task builds the one generic mechanism instead and must be able to retire all five as superseded.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

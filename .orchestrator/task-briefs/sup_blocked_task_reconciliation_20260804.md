# Task Brief: SUP-BLOCKED-TASK-RECONCILIATION-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add structured, checkable blockers and a generic blocked-task reconciliation pass
- Status: in_progress
- Owner: Codex
- Reviewer: Antigravity
- Next: Reopened by Human/Ops: PR #4582's two over-length commit subjects (88cf2725e, a50a90c7e) have been rewritten (now 342e20081, tree-identical, verified via git diff --stat against the prior head; check_commit_trailers.py passes) and force-pushed to the task branch. Branch CI Gate should now pass. Retry closeout.

## Summary
Root-cause fix for a pattern hit four separate times live on 2026-08-04: blocked is a one-way door in dispatch_ready_tasks (owned/review/finalize status lists never include it), so a task whose blocking condition later resolves (CI turns green, a dependency finishes) stays frozen until a human manually reopens it. The codebase already has five prior one-off 'reaper' tasks attempting fragments of this same fix, and all five are themselves stuck blocked as of today -- proof point patches don't compose. This task builds the one generic mechanism instead and must be able to retire all five as superseded.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

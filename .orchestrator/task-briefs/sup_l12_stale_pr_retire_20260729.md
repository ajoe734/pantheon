# Task Brief: SUP-L12-STALE-PR-RETIRE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Retire stale L12 PRs after 1025Z gap audit
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Reopened by Human/Ops: this task's waiting_for/blocked state predates the 2026-08-05 Codex-quota mass reassignment, which overwrote 'next' with the reassignment note but never re-examined whether the underlying block still applies -- and because blocked tasks are structurally invisible to dispatch_ready_tasks (root cause tracked in SUP-BLOCKED-TASK-RECONCILIATION-20260804), nothing has looked at this since. Re-verify the actual current blocking condition under the new owner/reviewer; re-block with an accurate reason if it genuinely still applies, otherwise continue.

## Summary
Retire or supersede stale L12 PRs without closing active product proof.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

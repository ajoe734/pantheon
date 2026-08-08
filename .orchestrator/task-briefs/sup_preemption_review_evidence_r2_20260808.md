# Task Brief: SUP-PREEMPTION-REVIEW-EVIDENCE-R2-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Redeliver exact-head rejection evidence without rewriting PR #4402
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Assignment created

## Summary
Publish the exact rejection-evidence net delta from failed PR #4402 through a fresh governed task branch and independent exact-head review, without rewriting stale history or touching PR #4399.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

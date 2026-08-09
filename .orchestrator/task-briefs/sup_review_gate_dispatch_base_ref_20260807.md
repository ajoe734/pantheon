# Task Brief: SUP-REVIEW-GATE-DISPATCH-BASE-REF-20260807

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dispatch canonical-review-gate workflow against base branch, not head
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Reopened by Human/Ops: waiting_for is stale -- it still names Antigravity, but this task's own next-note already recorded the reviewer reassignment to Codex2. Same recurring pattern as the 2026-08-05 mass reassignment: reassignment updates owner/reviewer but not waiting_for/blocked, and blocked tasks are invisible to dispatch until reopened.

## Summary
- `_dispatch_canonical_review_gate_workflow` dispatches the workflow definition from the reviewed PR's base branch while preserving the exact head branch and SHA as workflow inputs.
- PR #4605 and its committed evidence manifest are prepared for Codex2's independent exact-head review.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

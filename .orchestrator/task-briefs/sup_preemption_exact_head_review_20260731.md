# Task Brief: SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Independently review scheduler eligibility and grace exact head for live rescue
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Codex exact-head re-review found that provider local-CLI readiness blocks dispatch but not priority preemption at a924a6f3; preserve the finding, sync current dev evidence, and hand the task-scoped rejection packet to Codex2.

## Summary
Emergency independent review lane for the already implemented #4399 scheduler architecture fix. Human/Ops hands this review-only task to Antigravity so the true supervisor launches the reviewer without waiting on the closeout task that the old scheduler itself keeps killing.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

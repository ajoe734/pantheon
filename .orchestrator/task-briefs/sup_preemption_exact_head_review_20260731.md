# Task Brief: SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Independently review scheduler eligibility and grace exact head for live rescue
- Status: blocked
- Owner: Codex2
- Reviewer: Antigravity
- Next: Owner closeout verification passed, but the governed merge gate blocks PR #4402 because canonical `github.head_branch` still names the reviewed implementation branch `task/SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731` while the approved task PR uses `task/SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731`. Human/Ops must reconcile the task metadata or governed merge path without bypassing exact-head review; Antigravity remains the assigned reviewer.

## Summary
Emergency independent review lane for the already implemented #4399 scheduler architecture fix. Human/Ops hands this review-only task to Antigravity so the true supervisor launches the reviewer without waiting on the closeout task that the old scheduler itself keeps killing.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

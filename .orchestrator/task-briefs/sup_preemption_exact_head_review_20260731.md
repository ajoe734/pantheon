# Task Brief: SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Independently review scheduler eligibility and grace exact head for live rescue
- Status: review
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 must independently verify the blocking provider-readiness counterexample in exact-head-re-review-20260808.json. PR #4399 exact head a924a6f3 must not be re-approved; the archived source task needs a new corrective follow-up rather than in-place reopen.

## Summary
Emergency independent review lane for the already implemented #4399 scheduler architecture fix. Human/Ops hands this review-only task to Antigravity so the true supervisor launches the reviewer without waiting on the closeout task that the old scheduler itself keeps killing.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

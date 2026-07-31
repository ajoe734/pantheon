# Task Brief: SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Govern exact-head scheduler eligibility fix through live canary
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Verified PR #4399 (OPEN, MERGEABLE, 8/8 green checks: trailers, mirror guard, packaging, smoke acceptance). Code in supervisor.py & dispatch_policy.py unifies preemption & dispatch eligibility ladder, adds 5-min stability grace (priority_preemption_grace_seconds), respects cooldown and failure streak triage. Verified local tests pass: pytest test_supervisor.py test_dispatch_policy.py (486 passed, 4 subtests passed).

## Summary
Review and close out the existing #4399 architecture fix. It makes preemption and dispatch share the same eligibility ladder and protects new workers for five minutes; live acceptance waits for #4397 and a real canary.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

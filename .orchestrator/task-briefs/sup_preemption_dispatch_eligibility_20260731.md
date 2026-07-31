# Task Brief: SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Govern exact-head scheduler eligibility fix through live canary
- Status: review
- Owner: Codex
- Reviewer: Antigravity
- Next: Auto-reassigned SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731 away from unavailable lane Human/Ops (disabled, paused, sidecar-only, or auth-down); owner Human/Ops -> Codex.

## Summary
Review and close out the existing #4399 architecture fix. It makes preemption and dispatch share the same eligibility ladder and protects new workers for five minutes; live acceptance waits for #4397 and a real canary.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

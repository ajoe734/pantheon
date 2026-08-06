# Task Brief: LIFECYCLE-PROJ-STORE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Build the lifecycle projection relational store
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Reassigned to Claude. The branch was rebuilt on current dev after the prior head failed the range-scanning Commit trailers check on two 86-char subjects; all validation was re-run against real PostgreSQL 16.14 and the evidence manifest is rebound to Claude/Antigravity. Awaiting Antigravity's first independent exact-head decision on PR #4557.

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-STORE-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

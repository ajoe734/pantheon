# Task Brief: LIFECYCLE-PROJ-STORE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Build the lifecycle projection relational store
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner repaired both Codex2 blockers on PR #4503: mixed batches now apply only newly claimed receipt-owned mutations, and overlapping controllers atomically claim the global receipt before derived writes with the loser rolling back. Latest dev is merged; PostgreSQL 16 validation is green (19 focused, 138 adjacent, 3 migration/DML/EXPLAIN). Recut checksummed evidence retains Codex2's changes-requested decision on rejected head 3e525480 and now awaits Codex2's independent decision on the exact final repair head.

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-STORE-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: LIFECYCLE-PROJ-STORE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Build the lifecycle projection relational store
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review rejected at PR #4503 exact head 3e525480706ee0eb1cb5aadf583cb2a6c9670d1c. Existing real-PostgreSQL validation is green (17 focused, 136 adjacent, checksums and CI), but two blocking atomicity/idempotency defects reproduce against PostgreSQL 16: (1) a mixed batch containing one already-durable exact duplicate plus one new receipt still applies all caller-supplied journey mutations, so the duplicate rewrites the old journey status; filter/reject duplicate-owned derived mutations and add a mixed-batch regression proving old journey/stage/quarantine/revision truth is unchanged except for the new event; (2) two overlapping-scope controllers synchronized after the receipt pre-read both return success for one shared event, leaving 1 event_receipt but 2 journey rows because ON CONFLICT(event_id) DO NOTHING is not checked; claim/validate the global receipt before derived mutations or enforce equivalent DB/controller exclusion, and add a two-connection barrier regression proving exactly one atomic derived commit. Then refresh PR #4503 from dev (currently BEHIND), rerun required checks and real-Postgres tests, and recut the checksummed manifest for canonical reviewer Codex2 with the exact new head and independent decision instead of stale reviewer Antigravity/pending metadata.

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-STORE-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

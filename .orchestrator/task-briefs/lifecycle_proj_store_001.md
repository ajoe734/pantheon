# Task Brief: LIFECYCLE-PROJ-STORE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Build the lifecycle projection relational store
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex2
- Next: Independent review failed against real PostgreSQL. Required fixes: (1) make exact-duplicate processing truly idempotent—an existing same-fingerprint receipt must not rewrite stage/aggregate rows or increment revision; reviewer reproduced stage_status changing to tampered and revision 1->2; (2) enforce contiguous checkpoint advancement from durable dispositions—receiptless mutation advanced checkpoint 1->999; (3) enforce mode-owned freshness timestamps/accepted_live so backfill/recovery/replay cannot advance last_live_success_at; reviewer reproduced mode=backfill accepted_live=true doing so; (4) derive unresolved quarantine count from unresolved truth or idempotent deltas—retry left 1 unresolved row but controller count=2; (5) preserve deterministic out-of-order first/last source bounds—older input did not update first_occurred_at/first_ingested_seq; (6) use controller-scoped nonblocking advisory lock/readiness failure rather than a single global blocking pg_advisory_xact_lock; (7) add the plan-required identifier_type registry CHECK and a real least-privilege migration/runtime split (runtime constructor must not require DDL); (8) expand real-Postgres tests to cover rollback/retry, two writers, migration file applied twice, prior-reader compatibility, exact duplicate with mutations, checkpoint gaps, mode freshness, and all five indexed EXPLAIN paths; (9) publish checksummed evidence with exact commands/results plus PR, required checks, independent review, and merge evidence. Owner's declared command did pass 5 tests, but those tests do not cover these acceptance gates; no remote task branch or PR currently exists.

## Summary
Exact scope, non-goals, validation, rollout, and rollback are authoritative in tasks.json and LIFECYCLE-PROJ-STORE-001.md.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

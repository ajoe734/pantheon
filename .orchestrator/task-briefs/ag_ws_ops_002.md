# Task Brief: AG-WS-OPS-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governed Workshop research consultation and conclusion
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Review changes required after PR #3977. The resume path still has an unsafe adopted-consultation compensation gap: when a new-key retry adopts the original failed receipt's consultation and workshop complete_command then fails, router.py cancels that adopted downstream request but resolves only the current retry receipt; the original receipt remains resumable. A later retry therefore re-adopts the now-cancelled request and can return it as a successful open. Independent repro produced 503 initial partial failure -> 503 resumed commit failure with cancel -> 201 re-adoption, while the original source still had no resolved_at. In addition, find_resumable_command plus post-completion resolve is not an atomic claim/resolve; swallowed resolution-write failure or concurrent retries can reuse the same source, collide on the digest-derived event id, and cancel a consultation already committed by another retry. Required: atomically claim resumable lineage for one successor; resolve the source in the same transaction as successor completion/cancellation; do not cancel a merely adopted/shared resource unless exclusive effect ownership is proven; reject cancelled consultation as successful open; add Memory and Postgres-focused tests for resumed projection failure, resolution-write failure/concurrent new-key retries, and prove no stale resume, duplicate event, or cancellation of a successfully committed consultation. Verification: supplied partial-effects plus live-operations suites pass (30 passed); git diff --check passes.

## Summary
實作 research-runs、consultations、conclude 三條 deferred API，綁定 durable workshop version、真實 downstream lineage、idempotency 與 atomic terminal transition。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: SUP-PROGRESS-AWARE-FAILURE-STREAK-RETRY-20260801

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make same-owner reviewer retries progress-aware and bounded
- Status: in_progress
- Owner: Antigravity
- Reviewer: Human/Ops
- Next: Exact-head PR #4438 rejected at 94e3b6f34bda69711b16254f7623085bd41eecf5. The patch clears every streak only after an active worker commit, so it neither fixes the no-worker redispatch deadlock nor preserves auth/quota/policy fail-closed behavior. Implement exact owner/provider/progress-generation bounded eligibility with no worker/queue/lease, nonterminal-kind allowlist, replay consumption, durable audit, full negative matrix and clean evidence; fix failed 72-char subject gate. See PR comment.

## Summary
Close the durable dispatch gap exposed by PR #4434: a canonical exact-head reviewer reopen with new work must not remain permanently suppressed by historical same-owner generic exits, while genuine repeated failures, auth/quota faults, and live leases remain fail-closed.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

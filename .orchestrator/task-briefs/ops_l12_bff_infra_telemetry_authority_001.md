# Task Brief: OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add strict-auth infrastructure health telemetry authority
- Status: in_progress
- Owner: Claude
- Reviewer: Antigravity
- Next: Independent rejection of PR #4211 head 0d0b015c9: documenting that the readback used a volatile memory buffer does not satisfy AC3 (the admitted event is durable across retries and replicas). Current code accepts 202 and commits the durable ledger after InMemoryBuffer.put even though is_durable() is false; a process crash can erase the only event copy while the ledger causes every later retry to return duplicate 202. Required owner repair: fail closed before reservation/commit when the configured buffer is non-durable in authoritative infrastructure-health mode; tests may inject a fake durable broker contract but must not create a production-enableable volatile bypass. Add a real process/broker crash matrix covering crash before put, after durable put before commit, after commit, restart replay, and replica concurrency, with no false success/permanent loss. Re-cut evidence only after focused and full telemetry suites pass. No config change and no weakening strict auth/trading validation.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

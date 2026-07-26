# Task Brief: OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add strict-auth infrastructure health telemetry authority
- Status: todo
- Owner: Claude
- Reviewer: Antigravity
- Next: Implement the cross-service telemetry authority required by L12-BFF-001 within telemetry scope only. Define a non-trading infrastructure_health contract that does not invent RuntimeBinding fields. Require strict service JWT tenant binding and an allowlisted producer scope. Remove the shape-based binding bypass. Preserve all trading telemetry validation. Prove durable idempotent admission with real strict-auth route tests including spoof and restart/replica replay. L12-BFF-001 must drop its out-of-scope telemetry edits and consume this contract. Do not modify incidents because L12-EVO-001 currently owns that subtree.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

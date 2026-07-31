# Task Brief: SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Extend the program-specific guarded dispatcher for current-proof remediation
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: PR #4410 exact head 3c1af232d5480bef6b1162ffbda1c571b35e7dc8 was current at owner handoff, but dev advanced only by merged task-brief closeout PR #4413 to f5ca90ae5c8e9655b6fbff1dc69c776fd1a68495. Antigravity must merge current origin/dev into #4410 without force-push, verify the functional seven-file dispatcher delta is unchanged, rerun the current/legacy/catalog/schema/validate-only/dry-run gate, push, and hand off the new exact head. Do not edit config or materialize product tasks.

## Summary
Bootstrap the existing L12 program-specific dispatcher so the true supervisor can safely fan out the newly audited 28-task remediation DAG to auto workers. This bootstrap is dependency-gated on the scheduler runtime repair and is the only task sent through the generic bridge.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

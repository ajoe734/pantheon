# Task Brief: SUP-L12-DISPATCHER-AUTHORITATIVE-SNAPSHOT-SCALING-20260802

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Scale guarded L12 dispatcher authoritative state reads
- Status: in_progress
- Owner: Codex
- Reviewer: Human/Ops
- Next: PR #4502 review rejection requirements are implemented in this branch: task-only history is rebuilt with compliant subjects/trailers; dry-run uses a non-mutating validated snapshot; warm/stale-tail/missing/corrupt checkpoint regressions preserve journal, projection, checkpoint, and temp-file set; integrity/concurrency/full-replay/current-catalog and >=2GB evidence are refreshed. Exact-head CI and independent Human/Ops re-review remain. No materialization or merge.

## Summary
Remove the proven pre-admission full-journal replay from the guarded current-proof dispatcher while preserving authoritative hash-chain, checkpoint integrity, all-or-nothing catalog admission, and the exact 28-task product DAG.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

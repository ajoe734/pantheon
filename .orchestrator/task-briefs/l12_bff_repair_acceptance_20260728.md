# Task Brief: L12-BFF-REPAIR-ACCEPTANCE-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Finish L12-BFF-001 acceptance defects and proof drills
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Owner acceptance proofs pass on current dev with no additional product-code diff; publish the task-scoped evidence PR and obtain Codex2 exact-head review before merge and closeout.

## Summary
完成 L12-BFF-001 剩餘 acceptance defects 與 proof drills。

## Owner Result
- PR #4274 is merged as `7ba7b5e19fbd16aa36bf569c6a46d244eb9da3e1`.
- The 168-test focused BFF/telemetry/incidents suite passes on current dev.
- Nine L12-specific drills prove strict infrastructure admission, shared
  restart/replica dedupe, retry/DLQ/replay, complete registry coverage,
  error-rate triggering, target stop/recovery, and retention-safe incident
  resolution.
- Five incidents application-route tests prove non-trading create,
  idempotency/conflict handling, fake RuntimeBinding isolation, and canonical
  status-route resolution.
- Hosted deployment and hosted restart proof remain outside this follow-up
  evidence task and must be claimed only by the designated verifier/hosted
  delivery tasks.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

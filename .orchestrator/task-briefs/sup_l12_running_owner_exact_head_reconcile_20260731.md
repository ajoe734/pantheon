# Task Brief: SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile running-owner PR exact head before support closeout is counted
- Status: todo
- Owner: Codex2
- Reviewer: Antigravity
- Next: Helper-claimed by idle Codex2; previous owner Antigravity becomes reviewer.

## Summary
PR #4386 current head differs from the reviewed task row head; reconcile exact-head proof before treating running-owner reconcile as support evidence.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Exact-Head Checkpoint
- Observed canonical status: `in_progress`; owner `Codex2`; reviewer `Antigravity`.
- Stale reviewed PR head: `0528e5cab1df5386adfdb3113b8653411635fe86`.
- Current PR #4386 head: `2d5f692e960a22eef7c4b6d63002996a68468079`.
- Tree comparison found two files inherited from the rebased `dev` base and one
  task-owned README formatting-only change. Supervisor code, tests, task brief,
  evidence manifest, and validation log have identical blobs at both heads.
- Focused validation at the current head passed: commit trailers, diff hygiene,
  config boundary, and 7 running-owner reconciliation tests.
- Owner recommendation: exact-head approval is appropriate, but downstream
  support must remain uncounted until Antigravity independently binds approval
  to the current head and PR #4386 merges through the protected path.
- Evidence: `docs/deployment/evidence/twelve-loop-gap/SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731/evidence.json`.

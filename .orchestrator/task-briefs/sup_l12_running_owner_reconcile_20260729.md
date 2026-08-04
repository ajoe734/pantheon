# Task Brief: SUP-L12-RUNNING-OWNER-RECONCILE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile running workers with authoritative row owners
- Status: review_approved
- Owner: Antigravity
- Reviewer: Codex2
- Next: Auto-reassigned ownership from Codex2 to Antigravity after repeated Codex2 terminal: fatal: bad object d3178e6dd08f6945246d54675d379a63da6b00ea

## Summary
補上 row owner/reviewer 與 live worker_runner/run records 的 reconcile 機制，避免 helper/fallback 失敗後任務真相漂移。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

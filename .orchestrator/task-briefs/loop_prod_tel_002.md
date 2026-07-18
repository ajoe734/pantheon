# Task Brief: LOOP-PROD-TEL-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical loop-run and Trade Journey lifecycle projector
- Status: todo
- Owner: Codex2
- Reviewer: Claude
- Next: Chair reassigned owner from Antigravity to Codex2: Task is execution class with no human_required_roles or non_dispatchable flag; blocker text is stale Codex2-unavailable owner handoff, provider guardrails are clear, and Codex2 is currently exercised with reviewer Claude distinct. Task returned to todo for a blocked-owner rescue dispatch.

## Summary
從真實 signal/decision/order/fill/position/reconciliation append events 投影 canonical loop-run 與 Trade Journey；維持單一 identity chain，manual/cron rebuild 只能標示 backfill，不能成為 live truth。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

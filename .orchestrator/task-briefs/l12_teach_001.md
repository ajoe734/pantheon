# Task Brief: L12-TEACH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make Persona Teaching authenticated, tenant-safe, and HA
- Status: review_approved
- Owner: Antigravity
- Reviewer: Codex
- Next: Auto-reassigned ownership from Claude to Antigravity after repeated Claude terminal: {"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1785153600,"rateLimitType":"seven_day","utilization":0.79,"isUsingOverage":false,"surpassedThres

## Summary
為 teaching API/worker 加 inbound authority 與 tenant，將 session/job/replay 移入 authoritative store，讓 functional health 與真正 eval/commit 結果一致。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

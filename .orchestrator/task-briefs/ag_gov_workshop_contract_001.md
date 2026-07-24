# Task Brief: AG-GOV-WORKSHOP-CONTRACT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair Governance–Workshop approval and Registry identity contracts
- Status: todo
- Owner: Codex2
- Reviewer: Codex
- Next: Assignment created

## Summary
修正 Governance approval 與 Strategy Workshop 的空集合 target-type 合約，以及 Registry entry ID 被誤當 strategy ID 的語意錯置；補齊真實 public API、restart persistence 與 exact-pair hosted regression。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

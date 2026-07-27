# Task Brief: L12-GITHUB-REVIEW-BRIDGE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Antigravity is quota-blocked and Claude is temporarily quota-paused; dispatching to available real Codex worker so review bridge work does not stall.
- Status: todo
- Owner: Codex
- Reviewer: Codex2
- Next: Ownership updated

## Summary
Bind fleet reviewer decisions to GitHub review gates

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: Fresh release v2026.07.29.8 passed required checks and auto-merged through PR #4378 as master commit 2c9388e07b9a99ac2938d58a0edf6e4d34002dd5; 27 ancestry-proven stale promote PRs were retired. Merge the exact-head rollup repair and completed evidence through the final task PR, then run governed done.

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

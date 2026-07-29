# Task Brief: PAN-LIFECYCLE-RECOVERY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Recover lifecycle projection and freshness health
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Codex2 is finalizing the task-scoped evidence PR after exact-SHA governed run 29967962811 passed deployment, canonical stimulus/projection, authenticated BFF readback, restart persistence, and lease release.

## Summary
修復 ENOSPC 後仍停更的 lifecycle projector，加入安全 generation retention、可恢復 publish 與 readiness/freshness truth，並把 live rescue 正式經 PR 部署。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

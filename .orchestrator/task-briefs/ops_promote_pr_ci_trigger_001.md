# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex2
- Reviewer: Antigravity
- Next: PR #4262 已合併；fresh promote PR #4375 證明 exact-head dispatch 與 auto-merge，但 run 30450718720 暴露 workflow_dispatch 仍重掃已驗證 release history。需先合併 narrow trailer-range repair，再以新 immutable release 完成 required checks、master ancestry 與 stale PR retirement 證據。

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

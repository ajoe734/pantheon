# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review_approved
- Owner: Codex
- Reviewer: Antigravity
- Next: 獨立複核 PR #4262 exact head 6d6586da85ce3e2bb48870052d6e8c0bece0f195：已完整 compose 最新 origin/dev (24d9c547e)，25 unittest 與 73 pytest 本地重跑 100% 通過，REST 介面與 workflow 觸發機制實作正確。簽核 exact head 6d6586da8，退回 owner (Codex) 進行 final closeout。

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

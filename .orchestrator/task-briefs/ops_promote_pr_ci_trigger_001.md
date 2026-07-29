# Task Brief: OPS-PROMOTE-PR-CI-TRIGGER-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Repair promote PR CI trigger and auto-merge path
- Status: review
- Owner: Codex
- Reviewer: Antigravity
- Next: Antigravity 核准的 exact head 6d6586da85ce3e2bb48870052d6e8c0bece0f195 在整合前因 strict dev 前進而失效。Codex 已 anchor dispatch brief、無衝突 compose origin/dev c1e396495，並在 composed tree 重跑 25 unittest、73 pytest 與 compile/YAML/JSON/diff checks 全綠。提交與推送 refreshed evidence head 後，需重新取得 Branch CI、Antigravity exact-head 核准及 Human/Ops root-freeze context，再由 governed integrator 合併 PR #4262。

## Summary
修復 promote/* PR 沒有 required checks 導致 auto-merge 永遠卡住的 CI/dispatch 治理缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

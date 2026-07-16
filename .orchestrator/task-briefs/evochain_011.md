# Task Brief: EVOCHAIN-011

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Dev deploy + packet closeout
- Status: todo
- Owner: Antigravity
- Reviewer: Codex
- Next: Auto-reassigned ownership from Claude to Antigravity after repeated Claude terminal: Worker exited before the task reached a terminal status.. Task returned to todo until Antigravity starts a fresh run.

## Summary
部署整包到 dev（compose 更新、sweep scheduler 啟用、BFF/governance 重佈），live curl 驗證 freeze_orders/rollbacks/journal aggregate surface 全 ok、全球 TopBar 數據源徽章依據 running_jobs 狀態誠實顯示 SNAPSHOT DATA 或 LIVE（部分降級），hosted 截圖歸檔，彙整所有 PR 與 residual risks。deploy 未經 live 驗證不得宣告完成（babysit rule）。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status, activity, archive and lock writes are routed to the validated central root.

# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-verified 2026-07-04 (15th finalize-dispatch check; re-confirm commit (13)): still review_approved, no external state changed. PR #171 (ajoe734/execute-plans -> dev, head 67c0b048) still OPEN/MERGEABLE/CLEAN, zero reviews, no autoMergeRequest -- still blocked on human self-merge governance approval into ajoe734/execute-plans:dev (AI cannot self-merge; notification already sent, not repeating). AG-DYNUI-PROD-006 (hosted E2E screenshot gate this task's acceptance defers to, owner Codex) confirmed still todo/unstarted via live `ai_status.py show` (worktree ai-status.json mirror is stale and must not be read directly). Prior cycle's screenshot-evidence commit (f9d900d89) already merged into dev via PR #2885. Note-only per review-approved closeout gate; not calling progress/blocker/done.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

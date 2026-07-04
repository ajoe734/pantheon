# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-confirm review_approved closeout blockers (83): execute-plans PR #171 (headRefOid 67c0b0480d0999a2b8318c3d9ad44366f5b2f768) unchanged -- OPEN/MERGEABLE/CLEAN, integration-gate SUCCESS, zero reviews, autoMergeRequest null; still governance-blocked on human self-merge into ajoe734/execute-plans:dev (already notified per the task doc). AG-DYNUI-PROD-006 (hosted E2E, owner Codex) still todo/unowned -- second independent blocker for hosted desktop/mobile screenshot evidence this task's acceptance criteria still require. Worktree clean apart from this re-confirmation; branch HEAD is already an ancestor of origin/dev (own cycle-82 commit merged as PR #2938). No done action; zero functional changes this cycle.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

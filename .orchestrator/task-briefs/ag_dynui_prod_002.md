# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Cycle 77 re-confirm: pantheon PR #2934 (commit 12eeb03b0) confirmed merged into dev (2e939b89b, verified via `git merge-base --is-ancestor 12eeb03b0 origin/dev`). execute-plans PR #171 (headRefOid 67c0b0480d0999a2b8318c3d9ad44366f5b2f768) still OPEN/MERGEABLE/CLEAN, integration-gate SUCCESS, zero reviews, autoMergeRequest null -- unchanged, governance-blocked on human self-merge into ajoe734/execute-plans:dev. AG-DYNUI-PROD-006 (hosted E2E, owner Codex) still todo/unowned -- second independent blocker for hosted screenshots. No done action; zero functional changes this cycle.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

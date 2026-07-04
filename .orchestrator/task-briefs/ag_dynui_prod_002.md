# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Corrected closeout blocker after supervisor merge: ajoe734/execute-plans PR #171 (67c0b0480d0999a2b8318c3d9ad44366f5b2f768) merged into dev on 2026-07-04 at merge commit 467d930957bf109405fa50a5bc252ff66ec3a7ee after the integration gate passed. The prior "human self-merge" blocker is obsolete. Remaining closeout risk is hosted dev FE proof for the standalone shell, which is intentionally owned by AG-DYNUI-PROD-006 hosted E2E/publish gate rather than local-dev screenshots.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

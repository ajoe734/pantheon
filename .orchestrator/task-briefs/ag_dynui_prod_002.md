# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Cycle 89 re-verification: ajoe734/execute-plans PR #171 (67c0b0480d0999a2b8318c3d9ad44366f5b2f768) unchanged -- OPEN/MERGEABLE/CLEAN, integration-gate SUCCESS, zero reviews; still governance-blocked on human self-merge into ajoe734/execute-plans:dev (already notified, no new human action observed). AG-DYNUI-PROD-006 (hosted E2E, owner Codex) still todo/unowned -- second independent blocker for hosted desktop/mobile screenshot evidence. Cycle 88's pantheon-side commit 4c34a44e0 merged into dev via PR #2945 (confirmed MERGED via gh). This task has now cycled ~89 times solely re-confirming the same two blockers with no state change; flagging to human/chair that further re-dispatch will not make progress until a human either merges execute-plans PR #171 or an owner starts AG-DYNUI-PROD-006. No done action this cycle; task remains review_approved pending a human merge decision on execute-plans PR #171.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

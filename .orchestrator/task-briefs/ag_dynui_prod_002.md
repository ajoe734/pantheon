# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Cycle 90 re-verification: ajoe734/execute-plans PR #171 (67c0b0480d0999a2b8318c3d9ad44366f5b2f768) unchanged -- OPEN/MERGEABLE/CLEAN, integration-gate SUCCESS, zero reviews, still governance-blocked on human self-merge into ajoe734/execute-plans:dev. AG-DYNUI-PROD-006 (hosted E2E, owner Codex) still todo/unowned -- second independent blocker for hosted desktop/mobile screenshot evidence. Cycle 89's pantheon-side commit 9b25b3b25 opened PR #2946, but dev advanced 5 commits while it sat open and dev's branch protection has `required_status_checks.strict=true`, so PR #2946 stayed `mergeStateStatus=BEHIND` even with all checks green; merged `origin/dev` into `task/AG-DYNUI-PROD-002` this cycle to bring it current and unblock that pending auto-merge instead of leaving it stuck. No done action this cycle; task remains review_approved pending a human merge decision on execute-plans PR #171. Flagging: ~90 consecutive re-verification cycles with no state change on the core blocker -- further re-dispatch will not progress this task without human action on the execute-plans PR.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

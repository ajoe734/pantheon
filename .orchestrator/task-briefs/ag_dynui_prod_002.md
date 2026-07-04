# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-confirmed review_approved closeout blockers (check 42): no external state changed since check 41. execute-plans PR #171 (head 67c0b0480d0999a2b8318c3d9ad44366f5b2f768, unchanged) still OPEN/MERGEABLE/CLEAN, integration-gate SUCCESS, zero reviews, autoMergeRequest null -- still governance-blocked on human self-merge into ajoe734/execute-plans:dev. ToolSearch 'orchestrator_approval_broker self-merge approve' again returned no matching deferred tools. AG-DYNUI-PROD-006 (hosted E2E, owner Codex) confirmed still todo/unowned via live ai_status.py show -- screenshot-based acceptance criteria remain deferred to it per approved review notes. No done transition; note-only per review_approved closeout gate.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-verified again 2026-07-04 (owned_finalize_dispatch resume): PR #171 (ajoe734/execute-plans, task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant -> dev, commit 67c0b0480d) is still OPEN/CLEAN/MERGEABLE with integration-gate SUCCESS (completed 2026-07-04T01:16:11Z) but unmerged, and has zero PR reviews recorded. AG-DYNUI-PROD-006 (hosted E2E/screenshot gate) is still `todo` (owner Codex, depends on this task plus PROD-003/004/005), so its hosted desktop/mobile screenshots still do not exist. Nothing has changed since the prior re-verification: still blocked on (a) a human merging PR #171 (AI self-merge into dev is blocked by standing governance policy, not retried again this pass) and (b) AG-DYNUI-PROD-006 hosted proof. Leaving status at review_approved; using `ai-status.sh note` only, not blocker/progress. Needs human PR merge decision.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

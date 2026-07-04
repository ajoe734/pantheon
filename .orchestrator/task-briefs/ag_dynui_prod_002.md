# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-verified 2026-07-04 (owned_finalize_dispatch resume, 9th check): still review_approved, no change. PR #171 (ajoe734/execute-plans, task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant -> dev, commit 67c0b0480d) still OPEN/CLEAN/MERGEABLE, integration-gate SUCCESS, zero PR reviews, unmerged (AI self-merge into dev governance-blocked, needs human; push notification already sent on 5th check, not re-sending to avoid spam). AG-DYNUI-PROD-006 (hosted E2E/screenshot gate, owner Codex) still todo, so hosted proof required by this task's acceptance criteria does not exist. AG-DYNUI-PROD-005 still todo pending this task + AG-DYNUI-PROD-003 (both review_approved, not done). Cannot run done. Checked all other Claude-owned live tasks (OPENCLAW-CRON-WRITE-SCOPE, DEVLOOP-PAPER-BINDING-RESTORE-001): both status=blocked waiting_for Human/Ops, so there is no genuinely unblocked owned work available this cycle (the previously-referenced OCLAW-PMEM-001 does not exist in the live ai-status store; it was a stale worktree-mirror artifact, see [[project_ai_status_live_store_vs_worktree_mirror]]).

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-verified 2026-07-04 (owned_finalize_dispatch resume, 10th check): still review_approved, no change. PR #171 (ajoe734/execute-plans, task/AG-DYNUI-PROD-002-agora-standalone-shell-compliant -> dev, commit 67c0b0480d) still OPEN/CLEAN/MERGEABLE, integration-gate SUCCESS, zero PR reviews, unmerged (AI self-merge into dev governance-blocked, needs human; push notification already sent on 5th check, not re-sending). AG-DYNUI-PROD-006 (hosted E2E/screenshot gate, owner Codex) still todo, so hosted proof required by this task's acceptance criteria does not exist. AG-DYNUI-PROD-005 still todo pending this task + AG-DYNUI-PROD-003 (both review_approved, not done). Cannot run done. Checked all other Claude-owned live tasks (OPENCLAW-CRON-WRITE-SCOPE, DEVLOOP-PAPER-BINDING-RESTORE-001): both also status=blocked waiting_for Human/Ops, no genuinely unblocked owned work available this cycle. Note: the prior (9th) check completed and committed (c3a11229a) only ~50s before this dispatch woke again — the supervisor is re-issuing owned_finalize_dispatch faster than any external state (human PR merge, Codex's AG-DYNUI-PROD-006) could plausibly change; flagging for chair-review in case the dispatch cadence for idle review_approved-blocked-on-human tasks needs a longer backoff.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

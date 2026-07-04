# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-verified 2026-07-04 (10th finalize-dispatch check, ~50s after the 9th): still review_approved, no external state changed. PR #171 still OPEN/CLEAN/MERGEABLE, zero reviews, self-merge governance-blocked (needs human; notification already sent, not repeating). AG-DYNUI-PROD-006 hosted E2E gate (owner Codex) still todo, so hosted screenshot proof required by acceptance is still missing. AG-DYNUI-PROD-005 still todo. OPENCLAW-CRON-WRITE-SCOPE and DEVLOOP-PAPER-BINDING-RESTORE-001 both still blocked waiting_for Human/Ops. No unblocked owned work available. Flagged in task brief that the finalize-dispatch cadence for this human-blocked task may warrant chair-review backoff tuning.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

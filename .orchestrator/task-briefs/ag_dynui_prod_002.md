# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Re-verified 2026-07-04 (14th finalize-dispatch check): still review_approved, no external state changed. PR #171 (ajoe734/execute-plans -> dev) OPEN/MERGEABLE/CLEAN, integration-gate SUCCESS, zero reviews; unmerged because AI self-merge into dev is governance-blocked and needs a human (notification already sent, not repeating). AG-DYNUI-PROD-006 (hosted E2E gate, owner Codex) still todo, so the hosted screenshot proof this task's acceptance requires is still missing -- cannot run done. AG-DYNUI-PROD-005 (also Claude-owned) still todo, blocked on this task + AG-DYNUI-PROD-003 (review_approved, not done). No unblocked owned work available this cycle. Recommend chair-review add dispatch backoff for owned_finalize_dispatch on human-blocked review_approved tasks to reduce repeat-check churn (14 consecutive identical checks so far).

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

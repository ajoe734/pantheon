# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Second review failed: no EVOCHAIN-005 follow-up commit exists after 58a1d0121 / PR #3591, so all 02:12 blockers remain. Reproduced unauthenticated/unaudited canonical status transition returning 201, raw JWT persisted as identity in drawer rollback fallback, wrong internal rollback RBAC, a contract-valid deployment rollback failing canonical persistence only after its side effect, no canonical FreezeOrder write for ExecuteEvolutionAction freeze, and non-atomic/non-idempotent updates without append-only audit or partial-commit reconciliation. Focused 41 tests pass, but 4 existing executor tests fail and git diff --check fails. Push a new task PR with real BFF-to-internal-to-governance-to-read/journal RBAC/MFA/lifecycle/idempotency/concurrency/partial-commit regressions; see PR #3591 comment 4964735438.

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

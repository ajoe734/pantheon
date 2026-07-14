# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Pushed round-2 fixes for all 4 Codex change-requested findings on PR #3624 (commit c8a968a9f): rollback runtime_id derivation + executed/completed status normalization to fix idempotency; EvolutionActorRole(admin) crash now raises clean EvolutionDecisionError + admin->operator role mapping on the execute path; governance freeze/rollback POST now requires bearer auth, derives trusted identity, validates declared role against token roles, and enforces authority-status checks on create not just transition; removed approver-role/rejecter-role test aliases; fixed evidence doc env-var/test-count claims; added composition + negative RBAC/MFA regression tests. 291 tests pass (governance + bff + evolution_decision + evolution). Awaiting CI on PR #3624 and Codex re-review.

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

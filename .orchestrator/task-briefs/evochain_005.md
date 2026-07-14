# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Resynced task branch with origin/dev again (34 commits ahead, unrelated incident/postmortem/evolution work, no conflicts) to clear PR #3624's BEHIND mergeStateStatus a second time; merge is clean, only `evolution_decision.py` auto-merged with no manual conflict resolution needed. Re-ran governance canonical-write suites post-merge: services/control-plane/bff/tests/test_evochain_005_governance_writes.py (12), test_evochain_004_freeze_rollback_store.py (5), test_bff_governance_subrules_contract.py (14) all pass; services/control-plane/governance full suite 244 passed. Still no Codex review/comments posted on PR #3624; waiting on re-review before closeout.

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

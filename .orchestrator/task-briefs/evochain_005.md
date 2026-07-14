# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Resynced task branch with origin/dev (21 commits, unrelated work, no conflicts) to clear PR #3624's BEHIND mergeStateStatus; merge commit 5134ff088 pushed. Re-ran governance/bff/evochain_005 suites post-merge: 85 passed. PR #3624 mergeStateStatus now CLEAN; CI finished green on the fresh push (Commit trailers, Runtime mirror guard, Smoke acceptance all SUCCESS). Still no new Codex review/comments posted; waiting on re-review before closeout.

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

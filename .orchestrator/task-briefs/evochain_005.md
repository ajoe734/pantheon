# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Round-2 fix (commit c8a968a9f) pushed to PR #3624; CI green (Commit trailers, Runtime mirror guard, Smoke acceptance all SUCCESS). No new Codex review/comments posted yet. Waiting on Codex re-review before closeout.

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

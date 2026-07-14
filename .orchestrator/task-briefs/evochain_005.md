# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: review
- Owner: Antigravity
- Reviewer: Codex
- Next: BFF governance canonical writes implementation is complete, verified locally (85 pytest tests passed), and PR #3624 is open with all checks green. Handed off to Codex for review.

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

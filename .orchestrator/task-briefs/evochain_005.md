# Task Brief: EVOCHAIN-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governance write endpoints persist to canonical store
- Status: review_approved
- Owner: Antigravity
- Reviewer: Codex
- Next: Round-3 review changes requested on PR #3624: deployable governance JWT/JWKS auth is unwired; canonical routes bypass MFA and lack legal status transitions; operator active-freeze and approver/multi-role evolution execution paths fail; post-side-effect canonical-write failures can repeat rollback; public journal acceptance evidence/test counts need correction. Review: https://github.com/ajoe734/pantheon/pull/3624#issuecomment-4966801460

## Summary
把 BFF 的 freeze/rollback 治理 write endpoints（approve/execute/reject 路徑）改為寫入 EVOCHAIN-004 的 canonical store，含完整審計欄位（actor、identity、時間、來源 command）。寫入後 read 面即可見。

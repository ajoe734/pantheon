# Task Brief: LOOP-PROD-AUTH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Strict dev auth cutover and exact BFF build identity
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: PR #3617 merged into dev by human (ajoe734) at 2026-07-15T04:21:11Z, merge commit c77bba765. Round-2 fixes independently re-verified in prior session (all 4 blocking findings addressed, 178/178 tests pass). Code-level acceptance is APPROVED on merits. Note: AC-06/AC-08 hosted readback still requires human-provisioned deploy secrets + an approved Pantheon Nonprod Deploy run before proven-live maturity can be claimed; evidence.json correctly marks these blocked.

## Summary
沿用既有 /bff/auth/dev-login，將 hosted dev 切到 AUTH_STUB=false/strict；使用短效 role/tenant identities，移除 default all-role bearer，並在 /bff/version 暴露非敏感 git SHA、image digest、build time、environment、config posture。

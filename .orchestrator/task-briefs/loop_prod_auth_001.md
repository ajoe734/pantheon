# Task Brief: LOOP-PROD-AUTH-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Strict dev auth cutover and exact BFF build identity
- Status: todo
- Owner: Antigravity
- Reviewer: Claude
- Next: Auto-reassigned ownership from Codex to Antigravity after repeated Codex terminal: Codex usage limit reached. Task returned to todo until Antigravity starts a fresh run.

## Summary
沿用既有 /bff/auth/dev-login，將 hosted dev 切到 AUTH_STUB=false/strict；使用短效 role/tenant identities，移除 default all-role bearer，並在 /bff/version 暴露非敏感 git SHA、image digest、build time、environment、config posture。

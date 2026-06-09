# Task Brief: ASST-SKILL-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add provider re-auth as device-flow skill assistant.provider.reauth
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: PR #1183 merged into dev at fabc64ae954994e9dd7f0cfb5f3614a0773c13ac; waiting for Claude review_approved before owner can run done.

## Summary
新增 assistant.provider.reauth skill：以 service-user CODEX_HOME 執行 codex login --device-auth，安全回傳 verification_uri/user_code，背景追蹤登入完成並重新 probe readiness。

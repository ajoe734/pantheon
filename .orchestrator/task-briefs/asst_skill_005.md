# Task Brief: ASST-SKILL-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add provider re-auth as device-flow skill assistant.provider.reauth
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Implemented provider reauth surfaces; adding tests and validation

## Summary
新增 assistant.provider.reauth skill（kernel + control-mode gated）：adapter 以 service-user mount 的 CODEX_HOME 跑 codex login --device-auth，擷取 verification_uri/user_code 回前端、背景輪詢直到 token 寫入掛載目錄、成功後自動 re-probe readiness。憑證只在 operator 瀏覽器與 IdP 間交換，不經 BFF/FE。先做 device-auth headless 擷取 spike。

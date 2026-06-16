# Task Brief: BFF-MGMTAI-BOOTSTRAP-RESILIENCE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden Management AI store bootstrap against Postgres privilege errors
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Claude2 review approved: bootstrap() privilege degradation correct, all 7 tests pass, non-privilege errors still propagate, scope confined to bootstrap + tests. Return to Claude for finalization.

## Summary
把 services/control-plane/bff/assistant_conversation_store.py 的 PostgresAssistantConversationStore.bootstrap()（CREATE TABLE/INDEX）用 try/except 包起來：遇 psycopg InsufficientPrivilege 或非致命 DDL 錯誤時記 warning 並繼續，不要讓整個 BFF startup crash。根因：assistant_conversation_turns 表 owner 是 pantheon_management_ai，但 BFF 以 pantheon_app 連線(MANAGEMENT_AI_DATABASE_URL 空→退回共用 DATABASE_URL)非 owner→CREATE INDEX 失敗→BFF crash-loop(2026-06-15 dev 全 502 事故,已用 GRANT pantheon_management_ai TO pantheon_app 暫解)。修復需:(1)bootstrap 對 privilege/DDL 錯誤降級不致命;(2)若必要索引已存在則視為成功;(3)加單元測試模擬 InsufficientPrivilege 驗證 BFF 仍能啟動。

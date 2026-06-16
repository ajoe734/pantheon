# Task Brief: OPS-MGMTAI-DB-ROLE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix Management AI DB role: set MANAGEMENT_AI_DATABASE_URL or codify GRANT
- Status: review_approved
- Owner: Claude2
- Reviewer: Codex
- Next: Review approved; owner should finalize branch/PR after refreshing from origin/dev.

## Summary
MANAGEMENT_AI_DATABASE_URL 是空的→BFF Management AI store 以 pantheon_app 連線,但 assistant_conversation_turns 等表 owner 是 pantheon_management_ai→InsufficientPrivilege crash(已用手動 GRANT pantheon_management_ai TO pantheon_app 暫解)。正解:在 dev(及 staging/prod)env 把 MANAGEMENT_AI_DATABASE_URL 設成 pantheon_management_ai 連線,或把該 GRANT 寫進 DB bootstrap/migration,讓 DB 重建後不需手動 GRANT。

# Task Brief: MGMT-GAP-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Management session auth and RBAC contract consistency
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Supervisor resumed MGMT-GAP-009 for finalize after successful dispatch.

## Summary
修正 /bff/me 403 但其他 management BFF reads 200 的 session/RBAC 契約裂縫；dev gate token、tenant、roles 與 FE session bootstrap 必須一致 fail-closed。

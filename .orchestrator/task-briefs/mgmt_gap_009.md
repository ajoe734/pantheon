# Task Brief: MGMT-GAP-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Management session auth and RBAC contract consistency
- Status: todo
- Owner: Codex2
- Reviewer: Claude2
- Next: Helper-claimed by Codex2 while Claude2 is dispatch-paused.

## Summary
修正 /bff/me 403 但其他 management BFF reads 200 的 session/RBAC 契約裂縫；dev gate token、tenant、roles 與 FE session bootstrap 必須一致 fail-closed。

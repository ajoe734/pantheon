# Task Brief: BFFGAP-WORKFLOWS-HOOKS

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF GET /bff/workflows + GET /bff/hooks (P2)
- Status: todo
- Owner: Codex
- Reviewer: Claude2
- Next: Ownership updated

## Summary
新增兩個自動化登錄讀端點:GET /bff/workflows(workflow templates)、GET /bff/hooks(cron/hook registry)。端點回 canonical list 信封(data/items/page_info/meta);store 空時回明確 degraded 信封(status:unavailable,source:missing)非裸[];沿用既有 auth/CORS;更新 BFF_API_CONTRACT.md 與 OpenAPI;在 services/control-plane/bff/tests/ 加 contract test(仿 test_bff_management_cockpit.py)。main.py 約47k行,優先用新模組(console_gap/<feature>.py 暴露 APIRouter)+單行 include_router,降低衝突。 供 FE WorkflowTemplates / HookCronManager。

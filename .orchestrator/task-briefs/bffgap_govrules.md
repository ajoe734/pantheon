# Task Brief: BFFGAP-GOVRULES

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF governance sub-rules: permissions + memory + consult + route-policies (P1)
- Status: review
- Owner: Claude
- Reviewer: Codex
- Next: Implementation complete: 4 governance sub-rules read endpoints in console_gap/. Canonical list envelope + degraded when source=missing. 14 contract tests pass. BFF_API_CONTRACT §14 updated. Commit 066f7742.

## Summary
新增 4 個治理子規則讀端點:GET /bff/management/permissions、GET /bff/management/memory-governance、GET /bff/management/consult-rules、GET /bff/route-policies(list)。端點回 canonical list 信封(data/items/page_info/meta);store 空時回明確 degraded 信封(status:unavailable,source:missing)非裸[];沿用既有 auth/CORS;更新 BFF_API_CONTRACT.md 與 OpenAPI;在 services/control-plane/bff/tests/ 加 contract test(仿 test_bff_management_cockpit.py)。main.py 約47k行,優先用新模組(console_gap/<feature>.py 暴露 APIRouter)+單行 include_router,降低衝突。 供 FE governance/{permissions,memory,consult,policies} 頁。

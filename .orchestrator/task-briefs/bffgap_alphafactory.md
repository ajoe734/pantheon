# Task Brief: BFFGAP-ALPHAFACTORY

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF GET /bff/alpha-factory (P1)
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: endpoint shape, degraded envelope, auth guard, and contract test coverage all correct. All 4 contract tests pass. PR #1633 CI green. Returning to Claude2 for closeout.

## Summary
新增 GET /bff/alpha-factory:回 idea→strategy 看板(lanes/cards)。端點回 canonical list 信封(data/items/page_info/meta);store 空時回明確 degraded 信封(status:unavailable,source:missing)非裸[];沿用既有 auth/CORS;更新 BFF_API_CONTRACT.md 與 OpenAPI;在 services/control-plane/bff/tests/ 加 contract test(仿 test_bff_management_cockpit.py)。main.py 約47k行,優先用新模組(console_gap/<feature>.py 暴露 APIRouter)+單行 include_router,降低衝突。 供 FE AlphaFactoryBoard。

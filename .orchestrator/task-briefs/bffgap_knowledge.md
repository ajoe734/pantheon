# Task Brief: BFFGAP-KNOWLEDGE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF GET /bff/knowledge (P2)
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: GET /bff/knowledge implementation is correct and complete. Canonical envelope, degraded path, auth reuse, contract tests, and BFF_API_CONTRACT all pass review. Returning to Codex for finalization.

## Summary
新增 GET /bff/knowledge:回 knowledge inbox 條目。端點回 canonical list 信封(data/items/page_info/meta);store 空時回明確 degraded 信封(status:unavailable,source:missing)非裸[];沿用既有 auth/CORS;更新 BFF_API_CONTRACT.md 與 OpenAPI;在 services/control-plane/bff/tests/ 加 contract test(仿 test_bff_management_cockpit.py)。main.py 約47k行,優先用新模組(console_gap/<feature>.py 暴露 APIRouter)+單行 include_router,降低衝突。 供 FE KnowledgeInbox。

## Closeout Evidence
- Owner finalization: Codex confirmed the reviewed implementation remains present in `services/control-plane/bff/console_gap/knowledge.py`, `services/control-plane/bff/main.py`, `services/control-plane/bff/tests/test_bff_knowledge_inbox.py`, and `services/control-plane/bff/BFF_API_CONTRACT.md`.
- Validation: `pytest services/control-plane/bff/tests/test_bff_knowledge_inbox.py` -> 3 passed.
- Scope unchanged: no canonical architecture expansion; closeout only records reviewed BFF endpoint delivery evidence.

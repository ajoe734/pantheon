# Task Brief: AG-BE-RS-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Unified run/progress/result projection
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: implementation aligns with v4 ResearchRunProjection schema, SSE v1.3 event naming correct, no RuntimeBinding/order-route violations. 173 tests passed. Returning to Codex for finalization.

## Closeout Evidence
- Implementation PR: https://github.com/ajoe734/pantheon/pull/2092
- Implementation commit: 1fbaec61056f8318870cec0ee1323263c70d2472
- Merge commit: 61b78053d4af0da094ba9edd3b85693678cdb9d2
- Reviewer approval: Claude approved v4 ResearchRunProjection alignment, SSE v1.3 event naming, and no RuntimeBinding/order-route violations.
- Local verification: `git diff --check`
- Local verification: `python3 -m py_compile services/control-plane/bff/agora/research/router.py services/control-plane/bff/agora/strategy_workshop/router.py services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py`
- Local verification: `python3 -m pytest services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py services/control-plane/tests/agora/test_winner_branch_e2e_v13.py services/control-plane/tests/agora/test_agora_isolation_matrix.py -q` -> 173 passed

## Summary
依 SD §7.3 與 specs/agora/research_run_summary.schema.json 做統一 ResearchRunSummary 投影:run/progress/result/metrics/artifactRefs/evidenceRefs,§17.2 research-runs list/create,SSE progress。研究工具不得寫 RuntimeBinding(§7.4 §9 治理鐵律)。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

# Task Brief: AG-BE-TR-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governed TradingIntent / handoff
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved — D1 boundary, schema alignment, stage/type/queue semantics, idempotency all verified; 31/31 tests pass; returned to Codex for finalization

## Summary
依 SD §12.4/§21 與 specs/agora/trading_intent.schema.json 做 governed TradingIntent/handoff:requestedMode 僅 shadow/paper/canary_request/live_request,canary/live 只建 request 不送 order(TRADING_INTENT_NOT_ALLOWED 守門),idempotencyKey 必填。絕不從 BFF 發 broker order。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

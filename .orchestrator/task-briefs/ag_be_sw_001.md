# Task Brief: AG-BE-SW-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Workshop session/event persistence
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: 54/54 tests passing; CAS atomicity, privacy rule, mandatory headers, FK+indexes all verified. Approved and returned to Claude2 for finalization.

## Summary
依 SD §6.5 在 control-plane Postgres 建立 strategy_workshop_session / strategy_workshop_event / strategy_completeness_snapshot 三表與 persistence,event 只存 private_content_ref + redacted_summary(私人原文進加密 store,不落 event payload)。Workshop session 不複製 StrategySpec 真相,只引用 registry draft id。含 §22.6 索引 (workshop_id,created_at) 等與 §17.2 list/create/get workshop endpoint。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

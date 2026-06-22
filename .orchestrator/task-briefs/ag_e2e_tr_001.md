# Task Brief: AG-E2E-TR-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Winner-branch strategy -> full trading room E2E
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: Supervisor resumed AG-E2E-TR-001 for finalize after successful dispatch.

## Summary
依 SD §24.3 step 9-11 寫 winner-branch(賣家節點)策略→加入交易作戰室→產生/編輯/接受 dashboard recipe→產生交易事件與裝示的 E2E;斷言 governed intent(不下單)、widget 全來自 registry、score 用 A2 components。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

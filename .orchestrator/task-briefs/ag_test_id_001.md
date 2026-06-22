# Task Brief: AG-TEST-ID-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Cross-user and Management route isolation E2E
- Status: todo
- Owner: Codex
- Reviewer: Claude2
- Next: 依 v1.3 design-closure-round2 重新對齊:gate 在 AG-XR-OPENAPI-004(v1.3 bundle 合併+hash+型別生成)後,連同既有上游依賴一起滿足才解鎖。

## Summary
依 SD §24.3/§26 寫跨 repo 隔離 E2E:(1) user A 不能讀 user B 的 servant/strategy/journal(CROSS_USER_ACCESS_FORBIDDEN);(2) Agora token 不能打 /bff/management/*(AGORA_SCOPE_VIOLATION);(3) Management projection 只回 redacted,拿不到 raw prompt。作為 Phase 1 的驗收門,Phase 2 之前必綠。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

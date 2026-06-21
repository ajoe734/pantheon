# Task Brief: AG-BE-RS-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: ResearchPlan facade/router
- Status: todo
- Owner: Claude
- Reviewer: Claude2
- Next: 依 v1.3 design-closure-round2 重新對齊:gate 在 AG-XR-OPENAPI-004(v1.3 bundle 合併+hash+型別生成)後,連同既有上游依賴一起滿足才解鎖。

## Summary
依 SD §7.1/§7.2/§17.2 與 specs/agora/research_plan.schema.json 在既有 Research Orchestrator 上做 Agora ResearchPlan facade:plan create(draft/approve)、stage 規劃與工具路由(vectorbt/qlib/statsmodels/quantlib/finrl/rllib/ray_tune backendHint),不新增 duplicate worker。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

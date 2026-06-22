# Task Brief: AG-FE-TR-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Candidate review and entry/position/exit queues
- Status: todo
- Owner: Claude
- Reviewer: Codex
- Next: 依 v1.3 design-closure-round2 重新對齊:gate 在 AG-XR-OPENAPI-004(v1.3 bundle 合併+hash+型別生成)後,連同既有上游依賴一起滿足才解鎖。

## Summary
依 SD §12.2/§12.3 與 A2 score components 做 CandidateReviewDrawer + entry/add/reduce/exit 佇列卡;候選分數須顯示 A2 多 component(不可只顯示單一數字);裝示產生 governed TradingIntent(經 AG-BE-TR-002),不從 UI 直接下單。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。

# Task Brief: AG-FE-DB-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Drag/resize/add/remove/change chart editor
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved: DashboardGridEditor implementation passes all 16 tests, schema-compliant PersonalizationEvent emission, full drag/resize/add/remove/change-chart coverage. Returning to owner Claude for closeout.

## Summary
依 SD §9.1/§9.4/§9.8 做 DashboardGridEditor(react-grid-layout):drag/resize/add/remove/change-chart,佈局存成 WidgetPlacement(x/y/w/h/minW...);每次操作發 PersonalizationEvent(對齊 specs/agora/personalization_event.schema.json)。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。

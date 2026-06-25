# Task Brief: AG-FE-DB-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Widget conversation revision + before/after
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Review approved: WidgetRevisionDrawer implements SD §9 widget revision + before/after per spec; 17/17 tests pass; validate endpoint integration correct; no invented routes, fields, or RuntimeBinding touches. Returning to Codex2 for finalization.

## Summary
依 SD §9 §7.5(AG-FR-DB-003/004)做單一 widget 對話修改 + Before/After Preview:點選 widget→交代副人改呈現→產生新 WidgetSpec(經 AG-BE-DB-001 validate)→預覽前後差異。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。

## Closeout Evidence
- Implementation PR: https://github.com/ajoe734/pantheon/pull/1860
- Implementation merge commit: b0444fa5f690ba0fc28a6d434ebe6dc53b03a0a1
- Implementation task commit: eb2f018ad11b9e485060d7e0a75a4b13e81c36c6
- Verification: `npm --prefix execute-plans test -- src/agora/widgets` (17/17 tests passed)
- Verification: `npm --prefix execute-plans run build:agora` (passed)
- Closeout note: initial `scripts/ai-status.sh done` attempt hit worker-local execute-plans artifact routing that pointed outside this Pantheon task worktree.

# Task Brief: AG-FE-ID-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora auth/session/servant status shell
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Implemented identity.ts (GET /bff/agora/me + /bff/agora/capabilities via withStrictLiveOrMock), servant.ts (POST /bff/agora/servant/ensure; removed unsupported GET), AgoraApp.tsx (identity->servant boot sequence, status+command skeleton, no management refs), AgoraApp.test.tsx (8 tests all passing). Anchor commit ac5229dc. Ready to push and resubmit for review.

## Summary
依 SD §23 在 execute-plans 建立 Agora app shell:Agora-scoped auth(audience=pantheon-agora)、servant ensure/status、BFF client identity.ts/servant.ts(禁止頁面直接 fetch、strict no-fallback)。AgoraApp.tsx 顯示 servant 狀態與命令列骨架,不揭露 Management/資金池/RuntimeBinding。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。

# Task Brief: AG-FE-000

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Separate Agora/Management entry, build, auth audience
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Review failed: npm run build:agora passes after local npm install, but dist/agora still contains 93 management matches. Root cause appears to be execute-plans/src/lib/bff/agora.ts importing @/lib/bff-v1/paths, whose object bundles /bff/management/* route strings into Agora; AskPersonas also still contains assistant_management_answer. npm run build:management and npx vitest run src/lib/bff/managementAssistant.test.ts pass. Remove all Management route refs/literals from the Agora production bundle before resubmitting.

## Summary
依 SD §3.1/§23.1 在 execute-plans 把 Agora 與 Management 拆成兩個獨立 app entry/build:新增 agora-main.tsx/management-main.tsx、agora.html/management.html、vite.agora.config.ts/vite.management.config.ts 與 dev/build/test/gate npm scripts;設定 VITE_APP_KIND 與 VITE_AUTH_AUDIENCE。Agora production bundle 不得含 /management route code。先做 Phase 1 不搬目錄,Phase 2 monorepo 另議。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。

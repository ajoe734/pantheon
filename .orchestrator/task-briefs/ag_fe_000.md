# Task Brief: AG-FE-000

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Separate Agora/Management entry, build, auth audience
- Status: review (handed off to Codex 2026-06-20)
- Owner: Claude
- Reviewer: Codex
- PR: https://github.com/ajoe734/pantheon/pull/1771 (CI running)

## Fix Applied (2026-06-20)

Two root-cause fixes for review failure ("dist/agora contains 93 management matches"):

1. **`execute-plans/src/lib/bff/agora.ts`** — removed `import { paths } from "@/lib/bff-v1/paths"`.
   The entire `paths` object (including all `managementXxx()` route builders) was bundled into
   the Agora chunk because Rollup can't tree-shake within a single object export.
   Replaced with inline strings: `/bff/agora/ask` and `/bff/agora/ask/sessions/${encodeURIComponent(id)}`.

2. **`execute-plans/src/agora/pages/AskPersonas.tsx`** — removed `assistant_management_answer`
   result_surface branch. This management-specific response handler doesn't belong in the
   Agora bundle; unknown result surfaces fall through to the generic dev-doc result handler.

## Verification
- `npm run build:agora` → passes; `grep management dist/agora/assets/*.js` → 0 matches
- `npm run build:management` → passes
- `npx vitest run` → 5/5 tests pass
- `npx tsc --noEmit` → 0 errors

## Summary
依 SD §3.1/§23.1 在 execute-plans 把 Agora 與 Management 拆成兩個獨立 app entry/build:新增 agora-main.tsx/management-main.tsx、agora.html/management.html、vite.agora.config.ts/vite.management.config.ts 與 dev/build/test/gate npm scripts;設定 VITE_APP_KIND 與 VITE_AUTH_AUDIENCE。Agora production bundle 不得含 /management route code。先做 Phase 1 不搬目錄,Phase 2 monorepo 另議。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP，用 blocker（或向 reviewer handoff）把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。 【UI 一律照設計稿,不要自己發想】凡與畫面有關(頁面、route、layout、component、widget、chart、互動、文案、樣式)必須嚴格依 SD §9/§10/§11/§12/§23 的 IA/版面/元件規格、design-closure A3 widget_registry/chart grammar,以及 V10/V11 視覺參考實作;沿用既有 design tokens 與共用元件,不得自創畫面、元件、版面、route 或自由發揮樣式。設計稿沒涵蓋到的畫面或互動,先開 blocker 問清楚再做。

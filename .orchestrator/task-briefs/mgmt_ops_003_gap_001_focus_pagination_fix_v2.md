# Task Brief: MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix Persona Fleet focused pagination contract from dev
- Status: review
- Owner: Codex
- Reviewer: Antigravity
- Next: Ready for review: execute-plans PR #256 merged to dev as 30bc432f8a4e095e9947da4f076886828a2bcd58; fix commit 2a565262f4b8bc1c440c079d47483e7a28d3c414 is an ancestor. Focused query now sends q plus page_size=100 and reloads on persona changes; live-only/no-seed fallback remains unchanged. Verified npm test -- --run src/management/pages/oversight/PersonaFleetPage.test.tsx src/lib/bff-v1/__tests__/management.test.ts (49 passed); Dev FE Deploy and integration gate succeeded.

## Summary
從最新 execute-plans/dev 修正 Persona Fleet focused query 與分頁一致性，禁止 main 舊架構與 seed fallback 回流。

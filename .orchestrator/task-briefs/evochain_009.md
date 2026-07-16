# Task Brief: EVOCHAIN-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FE journal formal-entry fields + fixture badge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Review changes required: (1) e2e/evochain009.spec.ts still does not deterministically select a true fallback persona. Journal fetch accepts parseable empty or missing envelopes, reads only the default first page, and compares fleet ids only to exact target.id. Live data has zero exact intersections while three formal entries match personas through target substrings, so the first fleet row is chosen by accident. Validate canonical envelopes, inspect all pages or query candidate persona filters, and fail closed. (2) Preserve the real mobile-chromium viewport, avoid both projects overwriting one evidence PNG, and put page.goto inside the bounded retry and 60s budget. (3) _core.tsx must use Number.isFinite for before and after; typeof NaN is number and currently renders NaN despite acceptance. Add numeric-NaN coverage. (4) Obtain terminal authenticated chromium and mobile-chromium evidence after the fixes, then reconcile provenance and current FE/BFF identities and merge Pantheon PR #3685. Formal PNG originated in PR #3610; fallback blob is from PR #3650, so record re-verification honestly before re-handoff.

## Summary
演化日誌卡片渲染 formal entry 的完整欄位（risk_level、action_type、target version、approval 狀態），並對 origin: seed 的 entries 顯示 fixture 徽章。2026-07-10 的 fallback 卡契約（persona-fleet-summary 卡）保持不變。跨 repo task：repo 是 execute-plans。

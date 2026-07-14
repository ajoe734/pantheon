# Task Brief: EVOCHAIN-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FE journal formal-entry fields + fixture badge
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Address E2E test type check errors and hosted flakiness: resolved typescript-eslint any errors by typing items in e2e/evochain009.spec.ts, and added fetchJsonWithRetry and gotoWithRegionRetry helper functions to make BFF calls and navigation deterministic. Local Playwright run passed. execute-plans PR #349 and pantheon PR #3650 are open with auto-merge. Updated EVOCHAIN-009-fe-journal-cards.md to reconcile E2E changes, PR citations, and deploy run target details.

## Summary
演化日誌卡片渲染 formal entry 的完整欄位（risk_level、action_type、target version、approval 狀態），並對 origin: seed 的 entries 顯示 fixture 徽章。2026-07-10 的 fallback 卡契約（persona-fleet-summary 卡）保持不變。跨 repo task：repo 是 execute-plans。

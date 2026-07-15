# Task Brief: EVOCHAIN-009

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FE journal formal-entry fields + fixture badge
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Review changes required: the card implementation on current execute-plans dev cf3578d passes 43/43 focused tests and touched-file ESLint, and the archived formal/fallback screenshots visibly satisfy the UI states. Delivery is not ready: PR #349 at d4a32f4 is still OPEN DIRTY/CONFLICTING; its fallback probe accepts parseable 200 responses with empty items and then may select fleetItems[0] even when that persona has a formal journal entry, so the claimed deterministic fallback is not guaranteed. Merge current origin/dev into task/EVOCHAIN-009 non-force, resolve the single e2e/evochain009.spec.ts conflict by preserving current dev types plus the retry/navigation work, validate/retry required non-empty fleet data, select only a persona absent from journal targets (otherwise fail with diagnostics), obtain authenticated green chromium and mobile-chromium hosted runs, and merge PR #349. Reconcile EVOCHAIN-009-fe-journal-cards.md: current Antigravity/Codex ownership, actual PR #349 state/merge SHA, formal screenshot provenance, exact ESLint command including the E2E spec, and Pantheon PR #3650 merge 5c7ac28d. Then re-handoff for review.

## Summary
演化日誌卡片渲染 formal entry 的完整欄位（risk_level、action_type、target version、approval 狀態），並對 origin: seed 的 entries 顯示 fixture 徽章。2026-07-10 的 fallback 卡契約（persona-fleet-summary 卡）保持不變。跨 repo task：repo 是 execute-plans。

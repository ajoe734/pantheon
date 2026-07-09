# Task Brief: MGMT-OPS-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Performance attribution drilldown and diagnostics
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed PR #3068/#3070: attribution view model, confidence banner, split formal/fallback sections, source coverage, and actionable missing-holdings diagnostics meet MGMT-OPS-004 acceptance. Verified frontend: npx vitest run ManagementPerformanceReviewPanel.test.tsx (8/8 pass). Backend: pytest test_bff_mgmt_ops_001_operations_read_model_contract.py (11/11 pass). npm run build:management succeeds. Fallback persona label no longer hardcodes 'Crypto-Alt-Hunter'; regression test confirms. Approved and returned to owner Antigravity for finalization.

## Summary
修正從 Persona Fleet 點績效進去的頁面：formal attribution、fallback summary、missing holdings 與 degraded diagnostics 必須分清楚。

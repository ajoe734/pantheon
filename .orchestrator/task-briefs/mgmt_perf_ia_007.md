# Task Brief: MGMT-PERF-IA-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Migration cleanup and regression
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Verified execute-plans PR #270 (merged, commit 2fb71a1) and Pantheon evidence PR #3413 (commit 703a35a7d) against source: 19/19 focused unit tests pass, production build succeeds, 5/5 Playwright tests pass (re-ran locally on a clean preview port after an initial false failure traced to a stale leftover preview server from another worktree). ManagementOperationsNav fully removed with no dangling references; CapitalPoolDetailRoute/RankingFormulaDetailRoute/RebalanceDetailRoute confirmed defined and wired; compatibility aliases redirect correctly with query preservation; redirect telemetry event and expiry ownership fields present and tested; RankingDashboardPage removal is a documented explicit decision. All 4 acceptance criteria met.

## Summary
完成 legacy alias、dead page、secondary navigation、route baseline 與 mobile/desktop regression 清理。

# Task Brief: MGMT-PERF-IA-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Migration cleanup and regression
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Deps 003/004/005/006 all done/archived; stale block. Proceed with migration cleanup + regression.

## Summary
完成 legacy alias、dead page、secondary navigation、route baseline 與 mobile/desktop regression 清理。

## Current Evidence

- execute-plans anchor commit: `2fb71a1`
- removed duplicate secondary management navigation
- canonical detail routes now mount their owned components
- removed dead ranking dashboard implementation
- focused verification: 19 unit tests, production build, 5 Playwright tests

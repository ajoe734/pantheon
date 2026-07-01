# Task Brief: MGMT-LOAD-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF read concurrency isolation
- Status: in_progress
- Owner: Claude
- Reviewer: Claude2
- Next: Supervisor auto-started MGMT-LOAD-005 after successful dispatch.

## Summary
隔離 shell summary/Evidence/alerts/approvals/jobs 的同步 read aggregation；/health 不可被 management read fanout 卡住；慢路徑要 timeout/degraded 而不是拖住 unrelated routes。

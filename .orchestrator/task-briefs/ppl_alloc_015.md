# Task Brief: PPL-ALLOC-015

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Human Inbox false-empty: 37s aggregate + FE timeout state
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed new round: PR pantheon#3637 (N+1 fix in get_allowed_actions/get_review_summary via _NOT_SUPPLIED sentinel, regression test verified passing) + pantheon#3639 (CommandStore in-memory cache, safe single-process/single-writer, test verified passing) + execute-plans#347/#348 (useV5Live surfaces error state; HumanInboxPage distinguishes loading/error/degraded-empty/truly-empty; AbortSignal 5s client timeout wired through withStrictLiveOrMock so a timeout now shows Transport Unavailable instead of false-empty). Confirmed execute-plans integration-gate failures on #347/#348 are pre-existing/unrelated (lint error in e2e/evochain009.spec.ts, known-flaky F01/F13/focus-overlay specs), not caused by this diff. AC3 (degraded state visually distinct from empty) is now met. AC1/AC2/AC4 (live latency numbers, live browser walk, post-deploy verification) still require human-triggered FE redeploy. Approved.

## Summary
Human Inbox 假空：BFF 聚合 37s degraded，FE 逾時顯示無項目，操作者看不到 2 筆待審升級案；詳見 .orchestrator/task-briefs/ppl_alloc_015_human_inbox_false_empty.md

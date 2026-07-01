# Task Brief: MGMT-LOAD-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Frontend shell fanout reduction
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Review approved for owner closeout: shell-summary fanout reduction and route-primary-ready follow-up are merged with green PR checks; local focused review validated TopBar, JobProgressDrawer, and e2e/23 acceptance.

## Summary
TopBar 改接 shell-summary；summary unavailable 時 defer full list reads；JobProgressDrawer 不在 first route load 重複抓 /bff/jobs；drawer hydration 延後到使用者開啟或 primary content 後。

## Owner Closeout Evidence
- Execute-plans PR: https://github.com/ajoe734/execute-plans/pull/136
- Execute-plans merge commit: `75a943ed3fb007c61f056496e5b8f7dfdb305a53`
- Pantheon evidence PR: https://github.com/ajoe734/pantheon/pull/2705
- Pantheon evidence merge commit: `3f9c91f0c70f37e6645b14cf03611890e645df1a`
- Verified PR checks: execute-plans `integration-gate` success; pantheon `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, and `Orchestrator Sync` success.
- Owner closeout scope: record finalization metadata only. No runtime, frontend, route registry, or canonical architecture changes in this Pantheon closeout commit.

# Task Brief: PPL-ALLOC-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promotion and allocation workbench
- Status: review
- Owner: Claude
- Reviewer: Codex
- Next: Implementation complete and verified in ajoe734/execute-plans#251 (tsc/lint/vitest 1214 tests/build all green); pantheon-side evidence PR ajoe734/pantheon#3130 open with CI green. Please review both; note execute-plans#251 needs a human merge per cross-repo governance (AI cannot self-merge execute-plans PRs).

## Summary
把 Promotion & Allocation 擴成唯一操作工作台：paper candidates、real ranking、quarterly capital、emergency actions。

## Reviewer Evidence (Codex, 2026-07-11)

- Reviewed `ajoe734/execute-plans#251` and `ajoe734/pantheon#3130` against the task acceptance criteria.
- The workbench exposes recommendation/review/approved/applied rebalance workflow states, real-ranking current/target weights and cap reasons, and rebalance detail focus links.
- Reviewer follow-up fixed a stale allocation-policy evaluation when persona-fleet data resolved before persona-league scores; commit `436aa32eaa24b4f048ae0b08c8a46686ceb56659` adds a regression test proving re-evaluation from the complete input rows.
- `execute-plans#251` integration gate passed in 17m17s, including lint, unit/integration tests, build, contract drift, BFF probes, hosted acceptance, and Playwright E2E. Pantheon PR #3130 checks are green.
- Review disposition: approved. Remaining publication gate is owner closeout after the Pantheon evidence PR merges and a human merges `execute-plans#251` under cross-repository governance.

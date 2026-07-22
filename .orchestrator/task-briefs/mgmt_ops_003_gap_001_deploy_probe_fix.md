# Task Brief: MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix Persona Fleet hosted deploy probe regression
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 approved the Persona Fleet deploy-probe repair; owner Codex must finalize the merged delivery and close the task.

## Summary
修正預設 Persona Fleet 在 production 為空時錯誤顯示 non-production rows，保留明確 persona focus 切頁行為，重新發布並通過 hosted probe。

## Closeout Evidence

- Frontend repair: `ajoe734/execute-plans#254`, merged into `dev` as
  `e23aba15bf530a617135441602fcee86dec149df` on 2026-07-11.
- Focused pagination follow-up: `ajoe734/execute-plans#256`, merged into
  `dev` as `30bc432f8a4e095e9947da4f076886828a2bcd58`.
- Reviewer artifact:
  `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
  records `APPROVE`, 47 focused unit tests passing, and two hosted linked-page
  Playwright tests passing.
- Post-merge dev deploy run `29156996097` completed successfully. Its hosted
  probe recorded production-only default rows, a valid live banner, no
  non-production rows, two successful Persona Fleet BFF requests, no console
  errors, and zero failed network requests.
- The parent GAP-001 review addendum records the merge/deploy ancestry and
  supersession of the unmerged duplicate PR #255.

## Finalization Verification

- `gh pr view 254 -R ajoe734/execute-plans --json state,mergedAt,mergeCommit`
- `gh pr view 256 -R ajoe734/execute-plans --json state,mergedAt,mergeCommit`
- `gh run view 29156996097 -R ajoe734/execute-plans --json status,conclusion,headSha,url`
- `git diff --check`

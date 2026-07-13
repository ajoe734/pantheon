# Task Brief: MGMT-SSE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Authenticated live SSE transport for the management console
- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity
- Next: integration-gate run 29268056108 (on 7f13fa3) finished FAIL — only the same two pre-existing hosted flakes as the prior triage (`25-persona-fleet-live-linked-pages.spec.ts` ranking-table `無資料`, `agora-winner-branch-hosted.spec.ts` rollback-overlay intercept), unrelated to this task's `liveSse.ts`/`agora/workshops.ts` diff. Separately PR #300 had gone BEHIND dev again (+18 commits); merged origin/dev non-force (7f13fa3 -> 20f6b9e, clean, no conflicts) and pushed to task/MGMT-SSE-001. mergeStateStatus now MERGEABLE/BLOCKED (checks re-running on 20f6b9e), no longer BEHIND. Still awaiting green checks + human merge of PR #300 (self-merge governance-blocked).

## Summary
EventSource 帶不了 Authorization header→live SSE 必 401→頂欄 SNAPSHOT DATA 徽章；改 streaming fetch（liveSse.ts + agora/workshops.ts），修法範本 execute-plans PR #289。詳見 .orchestrator/task-briefs/mgmt_sse_001_authenticated_sse.md

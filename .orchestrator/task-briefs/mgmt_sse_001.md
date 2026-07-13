# Task Brief: MGMT-SSE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Authenticated live SSE transport for the management console
- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity
- Next: PR #300 went BEHIND execute-plans dev again (+3 commits, incl. PPL-ALLOC-015); merged origin/dev (ad206b2 -> 7f13fa3, clean merge, no conflicts) and pushed non-force to task/MGMT-SSE-001. The prior integration-gate run (on ad206b2) failed only on pre-existing flakes unrelated to this task's scope (persona-fleet ranking table `無資料`, Agora rollback overlay intercept) plus one SSE-reconnect 502 (hosted BFF gateway transient — reviewed `liveSse.ts` reconnect/backoff/Last-Event-ID logic directly, it is correct; not a code regression from this diff). New Branch CI Gate + integration-gate runs are in progress on 7f13fa3; awaiting green checks and then human merge of PR #300 (self-merge is governance-blocked).

## Summary
EventSource 帶不了 Authorization header→live SSE 必 401→頂欄 SNAPSHOT DATA 徽章；改 streaming fetch（liveSse.ts + agora/workshops.ts），修法範本 execute-plans PR #289。詳見 .orchestrator/task-briefs/mgmt_sse_001_authenticated_sse.md

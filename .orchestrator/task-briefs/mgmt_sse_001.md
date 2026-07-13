# Task Brief: MGMT-SSE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Authenticated live SSE transport for the management console
- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity
- Next: Recheck 2026-07-13 (round 5): PR #300 (execute-plans) still OPEN at head 20f6b9e (unchanged since round 4). mergeStateStatus CLEAN, all Branch CI Gate checks SUCCESS. Self-merge remains governance-blocked (per prior denial). No pantheon-side action available; awaiting human merge, then ai-status.sh done.

## Summary
EventSource 帶不了 Authorization header→live SSE 必 401→頂欄 SNAPSHOT DATA 徽章；改 streaming fetch（liveSse.ts + agora/workshops.ts），修法範本 execute-plans PR #289。詳見 .orchestrator/task-briefs/mgmt_sse_001_authenticated_sse.md

# Task Brief: MGMT-SSE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Authenticated live SSE transport for the management console
- Status: review
- Owner: Claude
- Reviewer: Antigravity
- Next: Auto-reassigned MGMT-SSE-001 away from unavailable lane Codex2 (disabled, paused, sidecar-only, or auth-down); reviewer Codex2 -> Antigravity.

## Summary
EventSource 帶不了 Authorization header→live SSE 必 401→頂欄 SNAPSHOT DATA 徽章；改 streaming fetch（liveSse.ts + agora/workshops.ts），修法範本 execute-plans PR #289。詳見 .orchestrator/task-briefs/mgmt_sse_001_authenticated_sse.md

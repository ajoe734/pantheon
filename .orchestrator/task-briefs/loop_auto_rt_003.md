# Task Brief: LOOP-AUTO-RT-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add runtime session reaper and restart alignment
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: All three acceptance criteria implemented and test-verified: stale session reaper active (missing/stale heartbeat detection), fresh session on restart (zombie force-close + new UUID), BFF terminal_reason/staleness projection with row_health degraded. 50 tests pass.

## Summary
清理 stale paper monitoring sessions，讓 worker restart 建立 fresh session，不再用 ended_at=null 當 liveness proof。

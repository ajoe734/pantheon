# Task Brief: LOOP-AUTO-RT-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add runtime session reaper and restart alignment
- Status: todo
- Owner: Codex
- Reviewer: Claude2
- Next: Live dispatch materialized; waiting for supervisor pickup

## Summary
清理 stale paper monitoring sessions，讓 worker restart 建立 fresh session，不再用 ended_at=null 當 liveness proof。

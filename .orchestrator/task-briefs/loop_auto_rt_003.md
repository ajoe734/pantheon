# Task Brief: LOOP-AUTO-RT-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add runtime session reaper and restart alignment
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Auto-reassigned review from Claude2 to Claude after repeated Claude2 terminal: {"type":"assistant","message":{"id":"fd6ca914-a67a-4e2c-ac81-5f723e3b0544","container":null,"model":"<synthetic>","role":"assistant","stop_details":null,"stop_reason":"stop_sequenc

## Summary
清理 stale paper monitoring sessions，讓 worker restart 建立 fresh session，不再用 ended_at=null 當 liveness proof。

# Task Brief: DEVLOOP-TELREAD

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF telemetry read real store (stop synthesize-on-read)
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved: real-store read path and fallback labelling are correct. Both contract tests pass. Returning to Claude for finalization.

## Summary
修 BFF /api/v1/telemetry:當 telemetry store 有真實事件時讀真實 store,不要 local_snapshot 現合成;保留 store 空時的 fallback 但標示 source。加測試。

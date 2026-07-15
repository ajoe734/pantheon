# Task Brief: EVOLOOP-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Strategy-driven signals for the promoted binding
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed cbe850e83 (merged PR #3662): strategy-driven signal path, fail-closed behavior, and signal-id traceability all verified by code read; two non-blocking follow-ups noted for the owner.

## Summary
讓被 promote 的 binding 的訊號來自它的策略 artifact(參數化邏輯),走正常 signal ingest 進 signal-store;僅對該 binding 停用通用 cron feeder(feed_signals*.sh 對其他 binding 維持不動)。證據:該 binding 的成交可追溯到策略發出的 signal。

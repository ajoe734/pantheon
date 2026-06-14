# Task Brief: DEVLOOP-L0-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Seed schema-v1 signals + drive drain, capture order events
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved and returned to owner Codex for finalization

## Summary
往 redis signal-store rpush 2~3 筆 schema-v1 信號(對應綁定 binding 的 symbol)，POST /api/runtime/drain 觸發 drain；確認 PaperExecutionAlgorithm 產生 order/fill 事件並出現在 /api/runtime/orders。

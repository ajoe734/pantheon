# Task Brief: DEVLOOP-L0-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Seed schema-v1 signals + drive drain, capture order events
- Status: review
- Owner: Codex
- Reviewer: Claude2
- Next: Auto-reassigned review from Claude to Claude2 after repeated Claude terminal: {"type":"assistant","message":{"id":"1fa6dfd0-6b13-4fee-a14f-46b774946094","container":null,"model":"<synthetic>","role":"assistant","stop_details":null,"stop_reason":"stop_sequenc

## Summary
往 redis signal-store rpush 2~3 筆 schema-v1 信號(對應綁定 binding 的 symbol)，POST /api/runtime/drain 觸發 drain；確認 PaperExecutionAlgorithm 產生 order/fill 事件並出現在 /api/runtime/orders。

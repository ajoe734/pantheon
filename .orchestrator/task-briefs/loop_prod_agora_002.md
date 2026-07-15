# Task Brief: LOOP-PROD-AGORA-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Implement six deferred Strategy Workshop operations
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude
- Next: Implementation anchor 7667ef849 records six live Strategy Workshop operations, durable command receipts, canonical adapters, consultation idempotency/compensation, and focused regressions. Validation: BFF 143 passed/1 skipped; consultation 65 passed; v1.8/compat 48 passed.

## Summary
實作 v1.5 六個目前故意 501 的 operations：GET/POST versions、select version、POST research-runs、POST consultations、POST conclude；全部走 canonical store/command 並更新 OpenAPI/bundle/compat manifest。

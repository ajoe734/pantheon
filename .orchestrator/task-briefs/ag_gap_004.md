# Task Brief: AG-GAP-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable Postgres store for dashboard recipes
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Changes requested: Postgres create idempotency is not atomic. Concurrent requests with the same Idempotency-Key can both pass has_idempotency_key(), insert distinct recipe identity/version rows, and only one idempotency mapping survives ON CONFLICT DO NOTHING. Reserve the key and create the recipe in one transaction (with replay bound to the recorded recipe), then add a concurrent same-key regression proving exactly one recipe is created and both requests resolve consistently. Focused reviewer checks: 7 passed; py_compile passed; docker-compose.yml parsed; docker-compose.control.yml uses supported !override tag and was not parseable by generic PyYAML.

## Summary
dashboard recipe 的 module-level dict 抽成 store 介面 + Postgres backend；保留 ETag/版本/rollback 語意。

# Task Brief: AG-GAP-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable Postgres store for dashboard recipes
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Review approved after atomic Postgres idempotency regression passed; owner Codex2 must finalize PR #3443 and close out after merge.

## Summary
dashboard recipe 的 module-level dict 抽成 store 介面 + Postgres backend；保留 ETag/版本/rollback 語意。

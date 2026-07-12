# Task Brief: AG-GAP-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable Postgres store for trading_room
- Status: todo
- Owner: Codex2
- Reviewer: Codex
- Next: Helper-claimed by idle Codex2; previous owner Codex becomes reviewer.

## Summary
trading_room in-memory 單例改為可選 Postgres backend（比照 PostgresWorkshopStore），保留 no_order_route_proof 與 ETag 不變式；live 重啟持久化證明。

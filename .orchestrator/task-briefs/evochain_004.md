# Task Brief: EVOCHAIN-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical store + read API for freeze orders and rollbacks
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Owner closeout audit found the new service clients could select the legacy evolution alias before the explicit governance URL; apply a narrow precedence fix and regression test, compose latest dev, then return to Claude for refreshed review.

## Summary
給 freeze_orders 與 rollbacks 一個 canonical 後端：governance service 持有 dataset 與 service read API；BFF read_store 對這兩個 dataset 增加 service_client 讀取路徑，local snapshot 降為 fallback-only。目標是 strict/live 模式下 surface 從 missing 變 ok。

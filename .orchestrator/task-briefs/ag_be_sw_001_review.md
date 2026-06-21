# Review: AG-BE-SW-001 — Workshop session/event persistence

Reviewer: Claude
Reviewed at: 2026-06-21
Verdict: **APPROVED**

## What was reviewed

- `services/control-plane/bff/agora/strategy_workshop/store.py` — MemoryWorkshopStore + PostgresWorkshopStore + factory
- `services/control-plane/bff/agora/strategy_workshop/router.py` — FastAPI route handlers
- `services/control-plane/bff/agora/strategy_workshop/__init__.py` — public re-exports
- `services/control-plane/bff/tests/test_agora_strategy_workshop.py` — 54 tests
- `services/control-plane/specs/agora/strategy_workshop.schema.json`
- `services/control-plane/specs/agora/strategy_completeness.schema.json`

## Verification run

```
cd services/control-plane/bff
python3 -m pytest tests/test_agora_strategy_workshop.py -v
```
Result: **54 passed** in 78.64 s.

## Checklist against acceptance criteria

| Criterion | Status |
|---|---|
| 可建立/讀取 workshop session 與 event | ✅ GET/POST /bff/agora/workshops, POST /workshops/{id}/messages, GET /workshops/{id}/events |
| event 不含私人原文(只 ref+redacted) | ✅ privacy_content_ref + redacted_summary only; no raw content field in DB or API |
| completeness snapshot 可持久化 | ✅ create_completeness_snapshot + get_latest_completeness_snapshot; GET /workshops/{id}/completeness |
| migration 與索引到位 | ✅ bootstrap creates all three tables + §22.6 indexes (user_tenant, workshop_created_at ×2) + FK constraints |
| 附測試 | ✅ 54 tests covering store unit, router integration, ETag/CAS concurrency, privacy, idempotency, mandatory headers |
| 實作與引用 spec/schema 逐欄位一致 | ✅ DB columns match spec; no invented fields/routes/enums |
| 無自創 schema/欄位/評分/widget/route | ✅ deferred routes return 501; no capability allowlist expansions |
| 不讓 Agora 直接下單/綁資金/寫 RuntimeBinding | ✅ implementation is read/write workshop data only |
| Workshop session 只引用 registry draft id | ✅ active_strategy_spec_registry_id is a ref, no StrategySpec content copied |

## Design quality notes

- **CAS concurrency (ETag/If-Match)**: Both `MemoryWorkshopStore.append_event_cas` and `PostgresWorkshopStore.append_event_cas` are properly atomic — memory version uses a single `threading.Lock`; Postgres version uses a single transaction with `UPDATE ... WHERE lock_version = %s ... RETURNING` + `INSERT` in one connection context.
- **Idempotency**: Both POST /workshops and POST /messages require `Idempotency-Key`; missing key → 400; duplicate key → 409.
- **Missing If-Match**: Returns RFC 6585 §428 Precondition Required, not 412.
- **409 body**: Includes `current_etag` and `latest_href` for client recovery.
- **Privacy rule tests**: `test_initial_event_has_private_content_ref`, `test_post_message_event_has_private_content_ref`, `test_event_list_never_exposes_private_content_key` all verify no raw content leaks.
- **Postgres FK retrofit**: The `ALTER TABLE ... ADD CONSTRAINT` blocks tolerate pre-existing FK (sqlstate 42710/42P16) and roll back the inner savepoint without aborting the overall bootstrap — reasonable resilience.
- **No outstanding concerns.** Implementation is clean and spec-faithful.

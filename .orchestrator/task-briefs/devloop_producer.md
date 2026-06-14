# Task Brief: DEVLOOP-PRODUCER

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Signal producer module: decision -> RedisPendingSignalStore
- Status: done
- Owner: Claude
- Reviewer: Claude2
- Next: Finalized. PR merged, task closed.

## Summary
新增 signal producer 模組，把 persona/strategy 決策輸出對應到 signal schema v1，enqueue 進 RedisPendingSignalStore。

## Implementation
- `services/execution/lean_runtime/signal_producer.py` — `DecisionSignalProducer` and `build_decision_signals`; normalizes persona/strategy decision dicts and dataclass/model objects into schema-v1 payloads and enqueues via `PendingSignalStore`.
- `services/execution/lean_runtime/pending_signal_store.py` — `PendingSignalStore` protocol, `InMemoryPendingSignalStore` (for tests), and `RedisPendingSignalStore` factory.
- `services/execution/lean_runtime/test_signal_producer.py` — 5 unit tests covering: multi-target allocation proposal, single-symbol LIMIT sell, missing limit_price rejection, `to_dict()` model acceptance, and Redis factory wiring.

## Acceptance Verification
```
python3 -m pytest services/execution/lean_runtime/test_signal_producer.py -v
# 5 passed in 0.69s
```
All three acceptance criteria are met:
1. signal_producer produces schema-v1 signals and enqueues them ✓
2. Unit tests cover enqueue + schema validation ✓
3. No live runtime dependency (InMemoryPendingSignalStore used in tests) ✓

## Review Notes (Claude2)
審查通過：5 tests green，schema-v1 欄位完整，persona metadata 正確傳遞，無 live runtime 依賴。Minor: _SCHEMA_PATH 死程式碼可後續清除，不影響本次驗收。

## Finalization
- Finalized by: Claude (owner)
- Verification: `python3 -m pytest services/execution/lean_runtime/test_signal_producer.py -v` → 5 passed
- Note: `_SCHEMA_PATH` dead code noted by reviewer; deferred to follow-up cleanup task.

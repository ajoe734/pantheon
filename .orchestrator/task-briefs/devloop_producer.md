# Task Brief: DEVLOOP-PRODUCER

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Signal producer module: decision -> RedisPendingSignalStore
- Status: in_progress
- Owner: Claude
- Reviewer: Claude2
- Next: Implementation complete; ready for review

## Summary
新增 signal producer 模組，把 persona/strategy 決策輸出對應到 signal schema v1，enqueue 進 RedisPendingSignalStore。

## Implementation
- `services/execution/lean_runtime/signal_producer.py` — `DecisionSignalProducer` and `build_decision_signals`; normalizes persona/strategy decision dicts and dataclass/model objects into schema-v1 payloads and enqueues via `PendingSignalStore`.
- `services/execution/lean_runtime/test_signal_producer.py` — 5 unit tests covering: multi-target allocation proposal, single-symbol LIMIT sell, missing limit_price rejection, `to_dict()` model acceptance, and Redis factory wiring.

## Acceptance Verification
```
python3 -m pytest services/execution/lean_runtime/test_signal_producer.py -v
# 5 passed in 0.92s
```
All three acceptance criteria are met:
1. signal_producer produces schema-v1 signals and enqueues them ✓
2. Unit tests cover enqueue + schema validation ✓
3. No live runtime dependency (InMemoryPendingSignalStore used in tests) ✓

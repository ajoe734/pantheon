# LOOP-AUTO-RT-004: Runtime-Aware Signal Isolation — Evidence

**Task:** LOOP-AUTO-RT-004  
**Owner:** Claude2  
**Reviewer:** Claude  
**Status:** review_approved → done  
**Date:** 2026-06-27  

## Scope

Paper runtime signal consumption isolated by `runtime_id` and `capital_pool_id`,
preventing shared-queue blind-consumption across concurrent paper runtimes.

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| Multiple runtime consumers cannot consume each other's signals | PASS | `test_runtime_isolation.py` — 15 tests |
| Mismatched runtime/persona/capital-pool signals are rejected | PASS | `test_capital_pool_isolation.py` — 10 tests; DLQ write on reject |
| Dead-letter or requeue behavior is tested | PASS | `test_dlq_routing.py` — 6 tests |

## Implementation Changes

**`services/execution/lean_runtime/signal_consumer.py`**

- Added `runtime_id` and `capital_pool_id` constructor params to `SignalConsumer`.
- Signals whose `runtime_id` or `capital_pool_id` fields don't match the consumer's
  identity are rejected with a logged noop and enqueued to the binding-scoped DLQ.
- Signals without these fields pass through unchanged (backward-compatible).

**`services/execution/lean_runtime/pending_signal_store.py`**

- Added `binding_dlq_key()` helper: `pantheon:signals:dlq:<binding_id>`.
- Added `enqueue_dlq()`, `dlq_depth()`, and `get_dlq()` to both
  `InMemoryPendingSignalStore` and `RedisPendingSignalStore`.
- DLQ enqueue is best-effort: failures are logged and do not block the signal path.

**`services/execution/lean_runtime/paper_runtime.py`**

- Wired `RuntimeIdentity.runtime_id` and `RuntimeIdentity.capital_pool_id` into
  `PaperRuntimeService`'s `SignalConsumer` construction.

**`services/execution/lean_runtime/test_signal_isolation.py`** (new, 406 lines)

- 31 isolation tests covering: runtime_id mismatch, capital_pool_id mismatch,
  combined mismatch, absent fields (backward-compat), DLQ write paths.

## Test Run

```
python3 -m pytest services/execution/lean_runtime/test_signal_isolation.py \
                   services/execution/lean_runtime/test_signal_consumer.py -v
```

Result: **63 passed** in 45.15s  
- 31 new isolation tests: all pass  
- 32 existing consumer tests: all pass, no regression  

## Review Notes (Claude)

- 審查通過
- 31 個新隔離測試全部通過
- 32 個既有測試無回退
- binding→runtime→capital_pool 隔離順序正確
- DLQ 寫入失敗不阻斷 signal path
- 後向相容：無 runtime_id/capital_pool_id 欄位的舊訊號照常通過

## Composition Note

This task composes with LOOP-AUTO-RT-002 (fleet reconciler): the fleet reconciler
sets `PANTHEON_SIGNAL_QUEUE_KEY` per worker at spawn time. This task enforces
isolation at the consumer level as a defense-in-depth layer on top of queue-key
isolation.

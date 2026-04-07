# FB-003: Fix Limit Application in Shared-Store Telemetry Queries

## Problem (From Codex Review)

The `TraderFeedbackStore.list()` method applies the `limit` parameter BEFORE filtering for event types. This caused a critical issue in shared-store scenarios:

When a shared feedback store contains many feedback events (approve, edit, reject, rationale) that appear before telemetry events, the `limit` can be completely consumed by feedback events, causing telemetry queries to return 0 results even when telemetry events exist in the store.

### Minimal Reproduction

```python
adapter = FeedbackStoreAdapter(feedback_store_path=store_path)

# Add 120 feedback events
for i in range(120):
    adapter.feedback_store.append({
        "event_id": f"fb-{i}",
        "event_type": "approve",
        "created_at": f"2026-04-07T00:{i % 60:02d}:00Z",
        "actor_id": "u",
        "actor_role": "approver",
        "channel": "console",
        "target": {"strategy_id": "strat-x", "promotion_state": "paper"},
    })

# Add telemetry event after all feedback
adapter.ingest_telemetry_event({
    "event_id": "evt-1",
    "event_type": "pnl_snapshot",
    "created_at": "2026-04-07T02:00:00Z",
    "execution_mode": "paper",
    "target": {"strategy_id": "strat-x", "promotion_state": "paper"},
    "metrics": {"pnl": 1.0},
}, "strat-x", "paper")

# Before fix: returns [] (empty!)
# Because TraderFeedbackStore.list() consumed limit=100 with feedback events
results = adapter.get_telemetry_for_strategy("strat-x")
```

## Solution

Modified three query methods in `FeedbackStoreAdapter` to:

1. **Request large limit from store** (`limit=1000000`) to avoid pre-emptive truncation
2. **Filter for telemetry event family** (pnl_snapshot, drawdown_snapshot, slippage_observation, fill_observation, order_rejection)
3. **Apply requested limit to filtered results**

### Changed Methods

#### `get_telemetry_for_strategy()`
- Now requests `limit=1000000` from `TraderFeedbackStore.list()`
- Filters results to `TELEMETRY_EVENT_TYPES` only
- Returns all matching telemetry events (no limit applied by store)

#### `get_telemetry_by_promotion_state()`
- Now requests `limit=1000000` from `TraderFeedbackStore.list()`
- Filters results to `TELEMETRY_EVENT_TYPES` only
- Returns all matching telemetry events for the state

#### `query_telemetry()`
- Now requests `limit=1000000` from `TraderFeedbackStore.list()`
- Filters results to `TELEMETRY_EVENT_TYPES` only
- Applies requested `limit` parameter to filtered results with `return results[:limit]`

## Testing

### New Regression Tests (3 critical tests added)

1. **`test_limit_applied_after_family_filter_get_telemetry_for_strategy`**
   - Adds 120 feedback events + 1 telemetry event
   - Verifies that telemetry event is returned (not lost to limit)

2. **`test_limit_applied_after_family_filter_get_telemetry_by_promotion_state`**
   - Adds 120 feedback events + 1 telemetry event for "paper" state
   - Verifies that telemetry event is returned

3. **`test_limit_applied_after_family_filter_query_telemetry`**
   - Adds 150 feedback events + 5 telemetry events
   - Queries with `limit=3`
   - Verifies that exactly 3 telemetry events are returned (not 0)

### Test Results

```
Ran 41 tests in 0.208s
OK

All smoke tests passed!
```

- ✓ All 38 existing tests still pass
- ✓ All 3 new regression tests pass
- ✓ Smoke test passes
- ✓ Event family separation confirmed
- ✓ Cross-process recovery verified
- ✓ Idempotency maintained

## Key Changes

**File: `services/telemetry/feedback_adapter.py`**

- Lines 162-213: Updated `get_telemetry_for_strategy()` to request `limit=1000000`
- Lines 230-261: Updated `get_telemetry_by_promotion_state()` to request `limit=1000000`
- Lines 275-347: Updated `query_telemetry()` to request `limit=1000000` and apply real limit to filtered results
- Added documentation explaining why limit is applied after filtering

**File: `services/telemetry/test_feedback_adapter.py`**

- Added `test_limit_applied_after_family_filter_get_telemetry_for_strategy`
- Added `test_limit_applied_after_family_filter_get_telemetry_by_promotion_state`
- Added `test_limit_applied_after_family_filter_query_telemetry`

## Commit

```
a35ab66 FB-003: Fix limit application in shared-store telemetry queries
```

## Contract Compliance

✓ **Event family separation**: Telemetry events remain strictly separated from feedback events in all query results
✓ **Shared store semantics**: Properly implements TraderFeedbackStore linkage while filtering for telemetry family
✓ **Limit guarantees**: Query limit is applied to telemetry results, not consumed by feedback events
✓ **Cross-process recovery**: Unchanged; still works correctly via `_recover_from_store()`
✓ **Idempotency**: Unchanged; duplicate event_ids still properly handled

---

See: `services/telemetry/review_fb003_codex_zh.md` for detailed Codex review context.

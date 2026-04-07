# FB-003: Shared Store Query Semantics Fix

## Problem Summary

The original implementation had a critical contract violation in shared-store query semantics: the `limit` parameter was being applied **before** telemetry event family filtering, not after.

### Root Cause

`TraderFeedbackStore.list()` iterates through the store and applies the limit as soon as it accumulates `filters.limit` matching events:

```python
def list(self, filters: QueryFilters) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in self.iter_events():
        if not self._matches(event, filters):
            continue
        events.append(event)
        if len(events) >= filters.limit:  # ← STOPS HERE
            break
    return events
```

The `FeedbackStoreAdapter` was then filtering for telemetry events **after** receiving truncated results:

```python
results = self.feedback_store.list(filters)
# Explicitly filter to telemetry event types only
results = [e for e in results if e.get("event_type") in TELEMETRY_EVENT_TYPES]
```

This created a scenario where if the shared store contained many feedback events before telemetry events, the store's `list()` would stop after reaching its limit on raw matches, never returning the telemetry events.

### Impact

Three query paths were affected:
- `get_telemetry_for_strategy()` - Could return empty if store had >100 feedback events for same strategy
- `get_telemetry_by_promotion_state()` - Could miss telemetry events if store had >100 feedback events for same promotion state  
- `query_telemetry(limit=N)` - Could under-return or return empty if N telemetry events didn't fall within first 1,000,000 raw matches

## Solution

Changed all three query methods to iterate through the store directly and apply the telemetry event family filter **before** the limit:

```python
# Pseudo-code of the pattern
results = []
for event in self.feedback_store.iter_events():
    # FIRST: Filter for telemetry family
    if event.get("event_type") not in TELEMETRY_EVENT_TYPES:
        continue
    
    # SECOND: Apply all other filters (strategy_id, promotion_state, etc.)
    if strategy_id and ...:
        continue
    
    results.append(event)
    
    # THIRD: Apply limit only after family + filter conditions pass
    if len(results) >= limit:
        break

return results
```

This ensures:
1. Feedback events never consume the query limit
2. All telemetry events matching the query filters can be returned (up to the limit)
3. The contract ("telemetry events are a separate event family") is properly enforced

## Changes Made

### Modified Methods in `feedback_adapter.py`

1. **`get_telemetry_for_strategy()`**
   - Removed use of `build_query_filters()` + `TraderFeedbackStore.list()`
   - Now directly iterates `iter_events()` with telemetry family filter applied first
   - Applies all filters (strategy_id, promotion_state, event_type, mode) before returning

2. **`get_telemetry_by_promotion_state()`**
   - Same approach: direct iteration with family filter applied first
   - Filters for telemetry event types before other conditions

3. **`query_telemetry()`**
   - Same approach for consistency
   - Applies family filter first, then all linkage filters, then time range filters
   - Only applies `limit` after all filtering conditions pass

### Added Regression Tests in `test_feedback_adapter.py`

Three new tests verify the fix works correctly:

1. **`test_shared_store_limit_applied_after_family_filter_get_telemetry_for_strategy`**
   - Adds 120 feedback events first
   - Adds 1 telemetry event after
   - Verifies the telemetry event is returned (not blocked by feedback events)

2. **`test_shared_store_limit_applied_after_family_filter_get_telemetry_by_promotion_state`**
   - Adds 150 feedback events
   - Adds 5 telemetry events
   - Verifies all 5 telemetry events are returned

3. **`test_shared_store_limit_applied_after_family_filter_query_telemetry`**
   - Adds 200 feedback events
   - Adds 10 telemetry events
   - Queries with `limit=3`
   - Verifies exactly 3 telemetry events are returned (not truncated by feedback events)

## Verification

- ✅ 44 unit tests pass (41 existing + 3 new regression tests)
- ✅ Smoke test passes with all acceptance criteria met
- ✅ Event family separation maintained (no feedback events leak into telemetry queries)
- ✅ Shared store cross-process recovery works correctly
- ✅ Idempotency preserved (duplicate event_ids don't repeat in queries)

## Contract Compliance

This fix ensures proper compliance with the feedback/telemetry family contract:
- `services/feedback/schema/contract.md:152` - Event families are separate
- `services/feedback/schema/contract.md:165` - Telemetry and feedback share linkage but not event types
- `services/feedback/schema/contract.md:226` - Query semantics must respect event boundaries

The query semantics now properly maintain the telemetry family boundary by applying it at the iteration/filtering layer, not at post-processing layer.

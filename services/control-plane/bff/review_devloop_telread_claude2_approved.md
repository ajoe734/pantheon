# Review: DEVLOOP-TELREAD — BFF telemetry read real store

Reviewer: Claude2
Date: 2026-06-14
Outcome: **Approved**

## Scope

Task: Stop synthesize-on-read for `/api/v1/telemetry`. When `telemetry_events` store has real records, read them directly; keep summary-projection fallback only when the event store is empty, and mark it `telemetry_summary_fallback`.

## Deliverables Reviewed

### 1. `read_store.py` — `list_telemetry_events_with_source`

Lines 15477–15513. Implementation:
- Calls `self._service.list_records("telemetry_events")` to get real event records.
- When `event_records` is non-empty, returns `(source, filtered_events)` where `source` comes from the actual service store — not from a synthetic projection.
- When the event store is empty, falls back to `_telemetry_summary_projection_events()` and returns `("telemetry_summary_fallback", ...)`.
- When both paths are empty, returns `("missing", [])`.

Logic is correct. The priority order is: real store → summary fallback → missing.

### 2. `main.py` — `GET /api/v1/telemetry` (lines 18782–18826)

- Calls `read_store.list_telemetry_events_with_source(...)`.
- When `source == "telemetry_summary_fallback"`, marks the surface `status = "degraded"` with an explicit `note` ("Telemetry event store is empty; served synthesized telemetry summary fallback.") and `staleness.served_from = "telemetry_summary_fallback"`.
- When the real store has data, surface is returned with the store source without degraded annotation.

Correctly surfaces both paths to the operator.

### 3. `test_devloop_telread_telemetry_contract.py`

Two tests cover the acceptance criteria:
- `test_api_v1_telemetry_prefers_real_event_store_over_summary_projection` — real event file with one event → endpoint returns that event with `source="service_store"` and `status="ok"`.
- `test_api_v1_telemetry_marks_summary_projection_when_event_store_empty` — empty event file → endpoint returns summary projection event with `source="telemetry_summary_fallback"`, `status="degraded"`, and note containing "event store is empty".

Both pass:
```
test_devloop_telread_telemetry_contract.py::test_api_v1_telemetry_prefers_real_event_store_over_summary_projection PASSED
test_devloop_telread_telemetry_contract.py::test_api_v1_telemetry_marks_summary_projection_when_event_store_empty PASSED
2 passed in 3.09s
```

## Acceptance Criteria Check

| Criterion | Status |
|---|---|
| store 有事件時回真實事件(非合成) | ✅ |
| store 空時回 fallback 並標示 source | ✅ |
| 測試覆蓋 real vs fallback 兩路 | ✅ |
| source 標示正確 | ✅ |

## Notes

No issues found. Implementation is minimal and correctly scoped to the `/api/v1/telemetry` read path. The `_telemetry_summary_projection_events` fallback is preserved and explicitly labelled as degraded — consistent with the BFF surface-status contract.

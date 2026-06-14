# Task Brief: DEVLOOP-TELREAD

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF telemetry read real store (stop synthesize-on-read)
- Status: review
- Owner: Claude
- Reviewer: Claude2
- Next: Implementation complete and merged in PR #1565. Deliverables: (1) read_store.py list_telemetry_events_with_source reads telemetry_events store when records exist; (2) main.py /api/v1/telemetry marks telemetry_summary_fallback source on the surface when event store is empty; (3) test_devloop_telread_telemetry_contract.py covers both paths. All tests pass. Please approve.

## Summary
修 BFF /api/v1/telemetry:當 telemetry store 有真實事件時讀真實 store,不要 local_snapshot 現合成;保留 store 空時的 fallback 但標示 source。加測試。

# Review: P0-TEL-PROJ-001 — Project paper telemetry into runtime status

**Reviewer:** Claude
**Owner:** Codex
**Date:** 2026-05-01
**Status:** APPROVED

---

## Acceptance Criteria

| Criterion | Result |
|---|---|
| Heartbeat ingest updates telemetry-owned runtime summary | PASS |
| Summary includes `runtime_binding_id`, `deployment_stage`, `engine_bridge_repo`, `engine_bridge_commit` | PASS |
| BFF runtime-state reads telemetry service summaries and shows `last_heartbeat_at` | PASS |

---

## Implementation Review

### `services/telemetry/runtime_summary.py`

`RuntimeSummaryProjectionStore.project_event()` correctly:
- Filters to `deployment_stage == "paper"` only
- Updates `runtime_binding_id`, `deployment_stage`, `engine_bridge_repo`, `engine_bridge_commit` from event payload and metadata
- Sets `last_heartbeat_at` when `event_type == "heartbeat"`
- Computes `health_summary` including bridge identity check
- Applies staleness via `_apply_staleness()` with configurable threshold
- Persists atomically via tmp-file rename

### `services/telemetry/ingest_svc.py`

`TelemetryIngestService.ingest()` correctly:
- Calls `self._runtime_summary_store.project_event(event)` after successful buffer enqueue (post-validation)
- Guards against projection exceptions without dropping the event
- Exposes `get_runtime_summary()` and `list_runtime_summaries()` pass-throughs
- Reports `runtime_summary_projection` stats in `stats()`

### `services/control-plane/bff/read_store.py`

`get_telemetry_summary()` correctly:
- Pulls from telemetry service via `PANTHEON_TELEMETRY_API_URL` → `/api/telemetry/runtime-summaries`
- Falls back to local snapshot when service is unavailable
- `telemetry_summaries` service client config matches telemetry main.py endpoint

### `services/control-plane/bff/main.py`

`/api/v1/operator/runtime-state` correctly:
- Calls `read_store.get_telemetry_summary(runtime_id)` per binding row
- Projects `last_heartbeat_at`, `runtime_binding_id`, `engine_bridge_repo`, `engine_bridge_commit` into response
- Marks surface status `degraded`/`unavailable` when telemetry service is missing

---

## Verification

```
python3 -m pytest services/telemetry/test_paper_runtime_ingest_contract.py -v
→ 5 passed

python3 -m unittest services.telemetry.test_runtime_summary_projection services.telemetry.test_main_routes -v
→ 4 + 10 passed

python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -v
→ 4 passed (including test_pkt010_runtime_state_board_reads_runtime_summary_from_telemetry_service)

python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py -q
→ passed

python3 -m pytest services/control-plane/bff/test_read_store_service_clients.py -q
→ 7 passed

git diff --check on touched files → clean
```

---

## Findings

No blocking issues. Implementation is minimal and correct:
- Paper-only filter in `project_event()` is intentional and correct per policy
- Thread-safe locking in `RuntimeSummaryProjectionStore`
- Atomic persist prevents corrupt reads during concurrent writes
- SA-17 §6.3 accurately documents the delivery scope

## Decision

**APPROVED** — task may proceed to closeout.

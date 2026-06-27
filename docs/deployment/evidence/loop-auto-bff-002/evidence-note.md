# LOOP-AUTO-BFF-002 Evidence Note
## Task: Add BFF downstream health monitor

**Owner:** Claude  
**Reviewer:** Codex  
**Status:** implementation complete, pending review  
**Date:** 2026-06-27

---

## Deliverable Summary

Implemented a continuous BFF downstream health monitor (`services/control-plane/bff/downstream_health_monitor.py`) that:

1. Probes configured downstream service health endpoints on a configurable interval
2. Emits `runtime_health` telemetry events to the telemetry ingest service for every probe result
3. Opens or updates incidents via the incidents service for sustained downstream failures (configurable failure threshold)
4. Runs as a non-blocking asyncio background task — BFF degraded mode does not affect active runtimes

---

## Acceptance Criteria Evidence

### AC-1: BFF downstream degradation emits telemetry
- **Implementation:** `_emit_telemetry_sync()` in `downstream_health_monitor.py` POSTs a `runtime_health` telemetry event to `POST /api/telemetry/ingest` on every probe cycle.
- **Sentinel values:** Uses `binding_id="bff-health-probe"`, `execution_mode="paper"`, `deployment_stage="paper"` to satisfy telemetry schema E-1–E-3 evidence contract fields. In environments with a configured `binding_store`, these events will be DLQ'd (expected; BFF health state is the primary truth surface for operator visibility).
- **Test coverage:** `TestTelemetryEmit::test_emit_telemetry_posts_to_ingest` verifies correct event payload structure and URL.

### AC-2: Health monitor can open or update incident for sustained failure
- **Implementation:** `_open_or_update_incident_sync()` POSTs to `POST /api/incidents` when `consecutive_failures >= failure_threshold` (default 3).
- **Idempotency:** Uses a stable sentinel incident ID (`bff-downstream-{target_name}-degraded`). Subsequent failures for the same target do not create duplicate incidents — tracked in `_open_incident_ids` and accepted as idempotent on HTTP 409.
- **Test coverage:**
  - `TestIncidentOpen::test_open_incident_on_sustained_failure` — first incident creation
  - `TestIncidentOpen::test_incident_idempotent_on_repeat_failure` — no duplicate POST on subsequent failures
  - `TestIncidentOpen::test_incident_409_treated_as_idempotent_ok` — 409 handled gracefully

### AC-3: BFF degraded mode does not affect active runtimes
- **Implementation:** All probe side-effects (telemetry emit, incident creation) run in `asyncio.to_thread()` — synchronous HTTP calls are offloaded to thread pool, non-blocking.
- **Error isolation:** `_probe_loop()` catches all exceptions; failures in one probe cycle don't stop the loop or affect BFF request handling.
- **Test coverage:**
  - `TestDegradedModeIsolation::test_probe_loop_error_does_not_propagate` — catastrophic probe_all failure doesn't crash the loop
  - `TestDegradedModeIsolation::test_bff_health_route_works_even_when_monitor_has_errors` — BFF `/health` returns 200 even with 10 consecutive failures on all targets

---

## Implementation Files

| File | Change |
|------|--------|
| `services/control-plane/bff/downstream_health_monitor.py` | NEW — DownstreamHealthMonitor class |
| `services/control-plane/bff/main.py` | ADD — import, module-level instantiation, startup/shutdown event handlers, `GET /bff/v5/downstream-health` route |
| `services/control-plane/bff/test_bff_downstream_health_monitor.py` | NEW — 26 contract and unit tests |
| `docs/deployment/evidence/loop-auto-bff-002/evidence-note.md` | NEW — this file |

---

## Verification

```
$ cd services/control-plane/bff
$ python3 -m pytest test_bff_downstream_health_monitor.py -q
26 passed, 20 warnings in 19.76s

$ python3 -m pytest test_bff_v5_loop_sentinel_contract.py test_loop_health_read_model_contract.py test_loop_inventory_read_model_contract.py test_pkt011_health_status_board_contract.py test_pkt013_operator_home_contract.py -q
32 passed, 12 warnings in ~30s
```

No regressions in existing health status board, loop health read model, or operator home tests.

---

## Design Notes

### Downstream targets
The monitor probes: `telemetry`, `incidents`, `runtime-manager`, `persona`, `deployment` — each resolved from the corresponding `PANTHEON_*_URL` env var. Targets not configured (empty URL) are silently skipped.

### Health probe endpoint
All probes hit `/{base_url}/__health__` — the canonical Pantheon service health endpoint registered by `services.foundation.health.register_fastapi_health_routes`.

### Telemetry sentinel values
The canonical telemetry schema (TEL-001) requires trading-execution-context fields that BFF infrastructure probes don't have. The monitor uses stable sentinel values. This is documented in the module docstring. If stricter telemetry isolation is needed in future, a dedicated BFF infrastructure telemetry topic can be added as a follow-up (LOOP-AUTO-BFF-002 follow-up: add BFF-specific telemetry write path).

### Operator visibility
The `GET /bff/v5/downstream-health` route exposes the current probe state to operators with `read` role. The monitor state is also available programmatically via `downstream_health_monitor.get_state()`.

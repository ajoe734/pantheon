# LOOP-AUTO-RT-005 Runtime Fleet Evidence Packet

Task: LOOP-AUTO-RT-005  
Owner: Codex  
Reviewer: Claude  
Generated: 2026-06-27T14:41:34Z

## Scope

This packet consolidates focused local evidence for runtime fleet recovery:

- stack or reconciler restart recreates workers for every active paper binding
- killing one worker restarts only that binding's worker
- paused or retired bindings stop their workers and are not restarted
- stale heartbeat evidence closes the old monitoring session and opens a fresh
  replacement session
- runtime signal consumption is isolated by binding, runtime, and capital pool
  identity with DLQ evidence for rejected signals

The packet is controller-level and contract-level evidence. It does not claim a
dev VM or production 15-runtime fleet drill, live broker authority, or
`proven-live` maturity.

## Verification Run

```bash
python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py services/execution/lean_runtime/test_signal_isolation.py services/execution/lean_runtime/test_signal_consumer.py services/control-plane/bff/test_pkt010_runtime_state_board_contract.py services/control-plane/bff/test_read_store_service_clients.py services/control-plane/bff/test_loop_health_read_model_contract.py -q
```

Result:

```text
114 passed in 39.28s
```

## Evidence Matrix

| Acceptance | Evidence | Result |
| --- | --- | --- |
| Stack restart recovery | `TestPaperFleetReconcilerAcceptanceCriteria.test_stack_restart_recreates_workers_for_all_active_bindings` starts a fresh reconciler with three active bindings and verifies three running workers. `docker-compose.yml` also defines the `paper-fleet-reconciler` service with `restart: unless-stopped` under the `paper-fleet` profile. | PASS |
| Kill-one-worker recovery | `TestPaperFleetReconcilerAcceptanceCriteria.test_killing_one_worker_restarts_only_that_worker` simulates SIGKILL/exit 137 for one binding, reconciles, and verifies exactly one replacement worker for the killed binding while the other worker is untouched. | PASS |
| Retire-binding stop | `test_paused_binding_stops_its_worker`, `test_retired_binding_stops_its_worker`, and the excluded-binding tests verify paused/retired bindings move out of desired state, terminate their workers, and do not restart when dead. | PASS |
| Heartbeat/session recovery | `test_stale_heartbeat_ends_session_and_restarts_worker`, `test_stale_persisted_zombie_session_is_closed_on_restart`, and `test_staleness_marker_does_not_count_as_live_session_on_restart` verify stale heartbeat evidence closes terminal sessions, terminates stale workers, and creates fresh sessions. BFF/read-store tests verify `terminal_reason` and `staleness.reason` are operator-visible. | PASS |
| Signal isolation | `TestPaperFleetReconcilerSignalQueueIsolation` verifies each spawned worker receives a binding-scoped Redis queue key. `test_signal_isolation.py` verifies binding/runtime/capital-pool mismatch rejection, DLQ routing, legacy unrouted signal compatibility, and DLQ replay inspection. | PASS |

## Source Evidence

- `docs/deployment/evidence/loop-auto-rt-002-fleet-reconciler.md`
- `docs/deployment/evidence/loop-auto-rt-003/README.md`
- `docs/deployment/evidence/loop-auto-rt-004-signal-isolation.md`
- `services/execution/runtime-manager/test_paper_fleet_reconciler.py`
- `services/execution/lean_runtime/test_signal_isolation.py`
- `services/execution/lean_runtime/test_signal_consumer.py`
- `services/control-plane/bff/test_pkt010_runtime_state_board_contract.py`
- `services/control-plane/bff/test_read_store_service_clients.py`
- `services/control-plane/bff/test_loop_health_read_model_contract.py`

## Boundaries

- No Docker Compose full-stack restart was executed in this worker.
- No dev VM paper fleet or broker-connected runtime was mutated.
- No live-capital execution was performed.
- This packet should not raise `capital_pool_execution` above its current
  maturity without a later environment-level fleet drill.

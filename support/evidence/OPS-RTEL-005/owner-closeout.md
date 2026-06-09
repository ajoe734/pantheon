# OPS-RTEL-005 Owner Closeout

Task: OPS-RTEL-005
Owner: Codex
Reviewer: Claude
Date: 2026-06-06

## Scope Delivered

- Added `runtimes[].row_health` to the BFF runtime-state board.
- Kept row health scoped to runtime binding, telemetry summary row, and paper
  runtime monitoring row.
- Kept `rollback_history` as board-level support-surface health under
  `meta.surfaces.rollback_history`.
- Added `meta.surfaces.runtime_state.support_surface_status`.
- Added `meta.surfaces.runtime_state.degraded_support_surfaces`.

## Acceptance Mapping

| Acceptance | Evidence |
| --- | --- |
| BFF can report fifteen runtime telemetry rows healthy while rollback history is unavailable | `test_pkt010_runtime_state_board_keeps_healthy_rows_when_rollback_surface_unavailable` creates 15 paper runtimes, returns all `row_health.status = ok`, and reports only `rollback_history` as a degraded support surface. |
| Board meta names degraded support surface | Same BFF test asserts `degraded_support_surfaces == {"rollback_history"}` and `support_surface_status.rollback_history = unavailable`. |
| Final restart and kill-one-worker recovery | `test_dead_worker_triggers_restart` proves a dead worker is replaced. `test_stale_heartbeat_ends_session_and_restarts_worker` proves a stale monitored worker is terminated and replaced with a new monitoring session. `test_stale_persisted_zombie_session_is_closed_on_restart` proves persisted stale monitoring evidence is closed on reconciler restart and a new active session is created. |
| Retire binding stop | `test_binding_retired_stops_worker` proves removing a binding from desired state stops the worker and leaves zero running workers. |
| Signal isolation | `test_spawned_workers_receive_distinct_queue_keys` proves distinct binding-scoped queue keys per spawned worker. `test_drain_discards_wrong_binding_signal` and `test_drain_executes_matching_binding_signal` prove consumer-side binding filtering. `test_build_auto_derives_from_binding_env` and `test_build_prefers_signal_queue_key_env_over_binding_env` prove queue-key derivation fallback and reconciler env priority. |

## Validation

```bash
python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
# 5 passed in 7.01s

python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_pkt010_runtime_state_board_contract.py
# passed

python3 -m json.tool docs/examples/PKT-010-runtime-state-board.json
# passed

python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -q
# 25 passed in 2.50s

python3 -m pytest services/execution/lean_runtime/test_signal_consumer.py -q
# 22 passed in 2.29s
```

## Boundaries

- No runtime-manager reconciler code changed in this task.
- No telemetry ingest writer, DLQ replay, or database migration semantics changed.
- No rollback storage writer or rollback authority changed.
- No signal execution code changed.
- The BFF route remains read-only.

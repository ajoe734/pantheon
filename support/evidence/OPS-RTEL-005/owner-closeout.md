# OPS-RTEL-005 Owner Closeout

Task: OPS-RTEL-005
Owner: Codex
Reviewer: Claude
Date: 2026-06-06
Finalization refresh: 2026-06-09

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

## Finalization Refresh - 2026-06-09

Central status was restored to `review_approved` after the earlier merged
closeout, so Codex repeated the owner closeout checklist before moving the task
to `done`.

- Delivered implementation PR: #1093, merged at
  `dcfdfdc559509c319a009ca38644172a8e1aa24b`.
- Previous closeout metadata PR: #1096, merged at
  `0f47a6564a8037e2db61e07e2f15fd2f54101d42`.
- Reviewer re-approval: `support/evidence/OPS-RTEL-005/claude-review.md`.
- Scope remains bounded to BFF runtime-state row health/support-surface truth
  plus already-delivered fleet and signal-isolation evidence; no canonical
  architecture docs, rollback writer, telemetry writer, or signal execution
  code changed in this finalization pass.

```bash
python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
# 5 passed in 3.12s

python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -q
# 25 passed in 2.18s

python3 -m pytest services/execution/lean_runtime/test_signal_consumer.py -q
# 22 passed in 2.31s

python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_pkt010_runtime_state_board_contract.py
# passed

python3 -m json.tool docs/examples/PKT-010-runtime-state-board.json
# passed

git diff --check
# passed
```

## PR #1212 Branch Refresh - 2026-06-09T12:25:44Z

PR #1212 was refreshed with latest `origin/dev` after PR #1214 moved the merge
target forward. The merge brought in supervisor status-root/watchdog files only
and did not change the OPS-RTEL-005 BFF runtime-state, runtime-manager, signal
consumer, telemetry, rollback, or canonical architecture surfaces.

Codex reran the focused owner validation after the refresh:

```bash
python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
# 5 passed in 3.39s

python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -q
# 25 passed in 3.06s

python3 -m pytest services/execution/lean_runtime/test_signal_consumer.py -q
# 22 passed in 2.50s

python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_pkt010_runtime_state_board_contract.py
# passed

python3 -m json.tool docs/examples/PKT-010-runtime-state-board.json
# passed

git diff --check
# passed
```

## PR #1212 Second Branch Refresh - 2026-06-09T12:26:49Z

PR #1212 was refreshed again after PR #1215 advanced `origin/dev`. That merge
brought in persona memory service/docs/tests and did not change the
OPS-RTEL-005 BFF runtime-state, runtime-manager, signal consumer, telemetry,
rollback, or canonical architecture surfaces.

Codex reran the focused owner validation after the second refresh:

```bash
python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q
# 5 passed in 2.78s

python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -q
# 25 passed in 2.28s

python3 -m pytest services/execution/lean_runtime/test_signal_consumer.py -q
# 22 passed in 2.00s

python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_pkt010_runtime_state_board_contract.py
# passed

python3 -m json.tool docs/examples/PKT-010-runtime-state-board.json
# passed

git diff --check
# passed
```

## Boundaries

- No runtime-manager reconciler code changed in this task.
- No telemetry ingest writer, DLQ replay, or database migration semantics changed.
- No rollback storage writer or rollback authority changed.
- No signal execution code changed.
- The BFF route remains read-only.

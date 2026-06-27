# LOOP-AUTO-RT-003 Evidence

Task: Add runtime session reaper and restart alignment
Owner: Codex
Reviewer: Claude2

## Delivered Behavior

- Paper runtime monitoring sessions with explicit stale evidence are terminal even when `ended_at` is still `null` or a stale producer sent `active: true`.
- The paper fleet reconciler force-closes stale marked sessions before opening a replacement session for a restarted worker.
- Reconciler-ended sessions now persist `ended_reason`, `terminal_reason`, and `staleness` evidence for operator readback.
- BFF runtime-state projection exposes monitoring `terminal_reason` and marks paper runtime rows degraded when the selected monitoring session is stale, ended, inactive, or otherwise terminal.
- ReadStore no longer treats `ended_at: null` or inconsistent `active: true` as liveness proof when stale evidence is present.

## Verification

```bash
python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py services/control-plane/bff/test_pkt010_runtime_state_board_contract.py services/control-plane/bff/test_read_store_service_clients.py services/control-plane/bff/test_loop_health_read_model_contract.py -q
```

Result after PR branch refresh: `50 passed in 33.64s`.

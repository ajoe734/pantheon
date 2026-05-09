# BFF-LUV-SEM-004 — v5 Loop And Sentinel Runtime Semantics

Date: 2026-05-09
Owner lane: runtime / worker ops integration
Reviewer lane: BFF contract review

## Problem

The v5 control-room paths are now registered, but loop-runs and sentinel findings are mostly derived or empty fallback surfaces. The frontend needs truthful runtime-backed status for the execution plans control room.

Affected routes:

- `GET /bff/v5/control-room`
- `GET /bff/v5/loop-runs`
- `GET /bff/v5/loop-runs/{id}`
- `GET /bff/v5/execution/persona-health`
- `GET /bff/v5/execution/strategy-health`
- `GET /bff/v5/sentinel/findings`
- `GET /bff/v5/sentinel/findings/{id}`
- sentinel remediation command routes

## Scope

- Wire loop-runs and sentinel findings to existing runtime/read-store sources or create the missing read-store adapter.
- Preserve safe degraded behavior when runtime/sentinel sources are absent.
- Make control-room compose the same data used by list/detail endpoints.
- Add tests for seeded runtime records, missing runtime source, and command idempotency.

## Non-Scope

- Do not activate live trading or real-capital execution.
- Do not invent separate frontend-only loop state.

## Acceptance

- v5 control-room is composed from the same loop, intervention, and sentinel read models as its child routes.
- Seeded loop and sentinel records are visible through list and detail endpoints.
- Missing runtime source produces explicit degraded metadata and no 500.
- Focused v5 tests and final live wiring tests pass.

## Implementation (completed 2026-05-09 by Claude2)

### read_store.py changes

- Added `loop_runs` and `sentinel_findings` to `ServiceBackedReadAdapter._DATASETS` with env/snapshot config.
- Added `list_loop_runs()`, `get_loop_run()`, `list_sentinel_findings()`, `get_sentinel_finding()` to `ServiceBackedReadAdapter`:
  - Primary: derive from incidents dataset (loop runs exclude "sentinel" title incidents; sentinel findings exclude "loop" title incidents).
  - Fallback: use dedicated `loop_runs`/`sentinel_findings` dataset if incidents unavailable.
  - Unavailable: return `(False, [])` / `(False, None)` when both sources absent.
  - Pattern-ID lookup: `loop-run-N` and `sentinel-finding-N` resolve to the Nth entry from incidents derivation.
- Added delegation methods to `ReadSurfaceStore` (forwards to `_service`).

### main.py changes

- `_sem_final_generic_list_for_path`: handles `/bff/v5/loop-runs`, `/bff/v5/sentinel/findings`, `/bff/v5/control-room`, `/bff/v5/execution/persona-health`, `/bff/v5/execution/strategy-health`.
- `_sem_final_generic_detail_for_path`: handles `/bff/v5/loop-runs/{id}` and `/bff/v5/sentinel/findings/{id}` with proper 404 when source is available but ID not found, and degraded DTO when source is missing.
- Control-room composes loops, sentinel findings, and interventions into a single read model.
- Persona-health and strategy-health aggregate from personas/strategy_specs read models.

### Tests

```
python3 -m pytest services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 12 passed

python3 -m pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py -q
# 15 passed

python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_final_command_execution_bridge.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py -q
# 53 passed
```

Total: 56 passed, 4 pre-existing warnings, 0 failures.

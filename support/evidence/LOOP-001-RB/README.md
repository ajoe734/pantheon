# LOOP-001-RB Evidence Packet

Task: `/bff/v5/loop-runs endpoint (rebaseline)`
Owner: Claude2
Reviewer: Codex2
Phase: Sprint 6 / EPIC-EVOLUTION

## Scope

Verified and fixed `/bff/v5/loop-runs` and related endpoints in the BFF control-plane service. The routes and read-store methods were already implemented; this task resolved a regression in the snapshot backfill logic.

## Routes Verified

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/bff/v5/loop-runs` | List loop runs derived from incidents or dedicated store |
| GET | `/bff/v5/loop-runs/{id}` | Detail with 404/degraded fallback |
| GET | `/bff/v5/sentinel/findings` | List sentinel findings derived from incidents or dedicated store |
| GET | `/bff/v5/sentinel/findings/{id}` | Detail with 404/degraded fallback |
| GET | `/bff/v5/control-room` | Composed loop + sentinel + interventions |
| GET | `/bff/v5/execution/persona-health` | Persona health list |
| GET | `/bff/v5/execution/strategy-health` | Strategy health list |

## Read-Store Methods

All implemented in `services/control-plane/bff/read_store.py`:
- `ServiceBackedReadAdapter.list_loop_runs()`
- `ServiceBackedReadAdapter.get_loop_run(loop_run_id)`
- `ServiceBackedReadAdapter.list_sentinel_findings()`
- `ServiceBackedReadAdapter.get_sentinel_finding(finding_id)`

Delegation in `ReadSurfaceStore.list_loop_runs()` / `get_loop_run()` / `list_sentinel_findings()` / `get_sentinel_finding()`.

## Regression Fixed

**File:** `services/control-plane/bff/read_store.py`
**Function:** `ReadSurfaceStore._backfill_local_contract_defaults`

**Bug:** When a snapshot explicitly provided `"incidents": {}` (an empty dict), `_merge_default_fixture_pack` would inject incidents from `fixtures_pack_c.json` (specifically `inc-pack-c-001`) into the empty dict. This caused `GET /bff/v5/loop-runs` to return fixture incidents instead of an empty list, violating the contract that an available-but-empty incidents source yields zero loop-run items.

**Fix:** Before calling `_merge_default_fixture_pack`, remove `"incidents"` from the fixture dataset when `self._data` already has an `"incidents"` key. This preserves explicit snapshot state.

## Verification

```
pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py -q
# 19 passed

pytest services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 14 passed

pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
       services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 33 passed
```

Before fix: `test_v5_loop_runs_empty_incidents_source_not_missing` failed (1/19).
After fix: 33/33 pass.

# LOOP-AUTO-RT-001 Evidence: Runtime Fleet Desired-State Query

**Task**: Define runtime fleet desired-state query  
**Owner**: Claude  
**Reviewer**: Codex  
**Date**: 2026-06-27

## Deliverables

### 1. `services/runtime-manager/fleet_desired_state.py`

Defines the canonical desired-state query contract:

- `FLEET_MANAGED_STAGES = {"paper", "canary"}` — stages under fleet management
- `FLEET_ELIGIBLE_STATUS = {"active"}` — only active bindings are desired
- `FLEET_EXCLUDED_STATUSES` — explicit exclusion reason per non-eligible status:
  - `retired` → `terminal_status`
  - `failed` → `terminal_status`
  - `pending_pause` → `draining`
  - `paused` → `draining`
- `PolicyEnvelope` — per-binding policy constraints (stage, allowed_scope, fleet_eligible, exclusion_reason)
- `FleetDesiredState` — full desired-state result with bindings + excluded lists
- `build_fleet_desired_state(bindings, stage_filter)` — stable query function

### 2. `GET /api/runtime-fleet/desired-state` (in `services/runtime-manager/main.py`)

New HTTP endpoint:
- Returns active fleet bindings with policy envelope
- Optional `?stage=paper|canary` filter
- Optional `?pool_id=...` filter
- Optional `?include_excluded=true` to inspect excluded bindings with reasons

### 3. `services/runtime-manager/test_fleet_desired_state.py`

33 unit tests covering:
- Fleet membership (paper, canary included; live, frozen, retired, failed, paused, pending_pause excluded)
- Stage filter behavior
- Invalid stage filters fail closed instead of returning an empty desired set
- Policy envelope fields
- `to_dict()` shape
- Idempotency / stability

### 4. `services/runtime-manager/test_runtime_manager.py`

HTTP route coverage verifies `/api/runtime-fleet/desired-state` returns active
and excluded bindings through the Flask auth/read path, and rejects invalid
stage filters with `400 INVALID_STAGE`.

## Verification

```
python3 -m pytest services/runtime-manager/test_fleet_desired_state.py services/runtime-manager/test_runtime_manager.py -v
```

Result: **89 passed** (2026-06-27)

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| Active runtime bindings queryable with stage and policy envelope | ✓ `build_fleet_desired_state` + `/api/runtime-fleet/desired-state` route test |
| Retired, paused, blocked bindings excluded or explicitly marked | ✓ `FLEET_EXCLUDED_STATUSES` with explicit reasons; returned in `excluded` list |
| Query stable for fleet reconciliation and tests | ✓ Idempotent; invalid stage filters fail closed; 89 focused tests passed |

## Non-Goals

- No live-capital execution
- Does not implement the reconciler (LOOP-AUTO-RT-002)
- Does not change existing `/api/runtime-bindings` behaviour

# LOOP-AUTO-EVO-003: Evolution Daily Sweep Worker — Evidence Note

Task-ID: LOOP-AUTO-EVO-003
Owner: Claude
Reviewer: Codex
Date: 2026-06-27
Branch: task/LOOP-AUTO-EVO-003

## Scope

Add a threshold/cooldown governed evolution daily sweep worker that:
- Proposes missing EvolutionDecision records for open incidents
- Enforces cooldown and active-decision single-active-rule
- Exposes last success, last failure, and proposal count

## What Was Already Built

The following were already present before this task:

| Component | File | State |
|-----------|------|-------|
| Sweep logic | `services/evolution/sweep.py` | Complete |
| Cooldown enforcement | `services/evolution/cooldown_enforcement.py` | Complete |
| Daily sweep API route | `services/evolution/main.py` `/api/evolution/daily-sweep` | Complete |
| Scheduler worker | `services/evolution/scheduler_worker.py` | Complete |
| Compose service | `docker-compose.yml` `evolution-daily-sweep-scheduler` | Present but unsupervised |

## Changes Made

### 1. `services/evolution/main.py`

Added module-level `_sweep_state` dict tracking:
- `last_success_at` — RFC3339 timestamp of last successful sweep
- `last_success_proposal_count` — proposals created in last successful sweep
- `last_failure_at` — RFC3339 timestamp of last failed sweep
- `last_failure_reason` — error message from last failure
- `total_sweeps_run` — cumulative sweep invocation count (process lifetime)
- `total_proposals_created` — cumulative proposals created (process lifetime)

Updated `/api/evolution/daily-sweep` to record sweep outcomes into `_sweep_state`.

Added `GET /api/evolution/sweep-status` endpoint exposing:
- All `_sweep_state` fields
- `scheduler_attach` block with worker module and compose service name

Updated `/livez` health metrics to include:
- `sweep_last_success_at`
- `sweep_last_failure_at`
- `sweep_total_proposals_created`

### 2. `docker-compose.yml`

Added `restart: unless-stopped` to `evolution-daily-sweep-scheduler` so the
scheduler is a properly supervised process that recovers from crashes.

### 3. `services/evolution/test_evolution_service.py`

Added 4 new tests:
- `test_sweep_status_initially_empty` — verifies null timestamps before first sweep
- `test_sweep_status_updates_after_successful_sweep` — verifies state update after sweep
- `test_sweep_status_accumulates_across_multiple_sweeps` — verifies counter accumulation
- `test_health_metrics_include_sweep_fields` — verifies `/livez` exposes sweep metrics

Added `_reset_sweep_state()` helper called by the `reset_store` autouse fixture to
prevent sweep state bleeding between tests.

## Verification

```
python3 -m pytest services/evolution/test_evolution_service.py -v
```

Result: **68 passed, 0 failed** (10.27s)

New tests:
```
test_sweep_status_initially_empty              PASSED
test_sweep_status_updates_after_successful_sweep PASSED
test_sweep_status_accumulates_across_multiple_sweeps PASSED
test_health_metrics_include_sweep_fields      PASSED
```

## Acceptance Criteria Mapping

| Criterion | Status | Evidence |
|-----------|--------|---------|
| Daily sweep proposes missing decisions under threshold policy | ✓ Pre-existing — `sweep.py` + `/api/evolution/daily-sweep` | `test_daily_sweep_threshold_fixture_creates_evolution_decision` |
| Cooldown and active decision lock are enforced | ✓ Pre-existing — `cooldown_enforcement.py` + `sweep.py` single-active-rule check | `test_daily_sweep_respects_cooldown_for_same_target_after_execute` |
| Sweep exposes last success, last failure, and proposal count | ✓ Added — `GET /api/evolution/sweep-status` + `/livez` metrics | `test_sweep_status_*`, `test_health_metrics_include_sweep_fields` |

## Maturity Transition

`api-only` → `scheduled`

The scheduler worker (`evolution-daily-sweep-scheduler` compose service) was
already present but unsupervised. Adding `restart: unless-stopped` and the
sweep-status metrics satisfies the `scheduled` maturity bar: a governed
scheduled process with observable last-success/failure liveness state.

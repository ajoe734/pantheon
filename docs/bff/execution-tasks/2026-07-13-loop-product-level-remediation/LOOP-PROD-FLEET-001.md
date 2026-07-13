# LOOP-PROD-FLEET-001 — Fair, quota-aware, starvation-bounded fleet admission

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `718504a73fe203e2e99acb524231d79475db0c6a6cedb61295c5ab2554d50812`
The catalog acceptance, proof, and dispatch arrays are machine-authoritative;
the prose sections below are explanatory renderings.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex |
| Reviewer | Codex2 |
| Wave | 1 |
| Fleet lane | `fleet-fairness-admission` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | hot review retries can consume reservations and starve older ready work |
| Target maturity | reconciled |

## Product outcome

Supervisor admission must be age-aware, retry-safe, owner/reviewer separated,
and quota-reset aware so one repeatedly failing task cannot starve later ready
loop work or make capacity telemetry lie.

## Dependencies

- `LOOP-PROD-WORKER-001`

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `.orchestrator/supervisor.py`
- `.orchestrator/runtime_state.py`
- `.orchestrator/test_supervisor.py`
- `.orchestrator/test_runtime_state.py`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-FLEET-001`

## Acceptance

- eligible-ready age starts on the first cycle where dependencies are done, the required role and provider are enabled, quota is not paused, and compatible capacity exists; ineligible intervals do not accrue age
- the oldest eligible task is admitted within two compatible-capacity admission opportunities and no task remains eligible for more than ten scheduler cycles or ten minutes, whichever occurs first
- three failures of the same task, role, and payload signature within fifteen minutes quarantine that signature for thirty minutes; one canary retry is allowed after cooldown and a new failure doubles cooldown up to two hours
- owner, reviewer, and recovery reservations derive from controller truth and release exactly once
- quota reset hints suspend only the affected provider lane and cannot leave sticky cross-task pauses
- status exposes ready age, admission reason, retry quarantine, quota reset, and starvation SLO breaches
- restart preserves the original eligibility clock, opportunity count, failure window, and cooldown without replaying stale admission
- deterministic mixed-fleet, hot-retry, saturation, quota, restart, cancellation, and corrupt-state tests pass
- a live mutation-free trace proves the fixed eligibility, two-opportunity, ten-cycle, ten-minute, three-failure, and thirty-minute bounds

## Required proof

- focused and adjacent supervisor validation
- deterministic scheduling traces and restart proof
- live mutation-free queue probe
- merged PR, merge SHA, checks, review, and checksummed evidence

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-FLEET-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- consume `LOOP-PROD-WORKER-001` admission/CAS primitives instead of creating a parallel state machine
- do not change product task dependencies to hide scheduler starvation
- quarantine must remain observable and recoverable, never silently drop work
- reviewer replays the starvation scenario against the exact head

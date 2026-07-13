# LOOP-PROD-FLEET-001 — Fair, quota-aware, starvation-bounded fleet admission

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

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

- ready owner and reviewer queues have deterministic age/fairness ordering with bounded priority aging
- hot task/signature retries trip a circuit breaker or quarantine and cannot monopolize global or per-fleet reservations
- owner, reviewer, and recovery reservations are counted from controller truth and released exactly once
- quota reset hints suspend only the affected provider/lane and resume without sticky pauses or cross-task mutation
- status exposes ready age, admission reason, retry quarantine, quota reset, and starvation-SLO breach
- restart preserves fairness age and circuit state without replaying a stale admission
- deterministic tests cover hot retry, old review, saturated owner, quota pause, mixed fleets, restart, cancellation, and corrupt state
- a live dry-run trace demonstrates an old ready review enters service within the declared SLO

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

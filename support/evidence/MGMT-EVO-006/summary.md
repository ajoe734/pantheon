# MGMT-EVO-006 Observation Window Report

Task: `MGMT-EVO-006`
Owner: `Codex2`
Reviewer: `Claude`

## Scope

Implemented a read-only Evolution service observation-window report:

- `GET /api/evolution/proposals/{decision_id}/observation-report`
- Requires the parent `EvolutionDecision` to be `executed`.
- Reports observation/cooldown window bounds, open/elapsed state, active blocking, convergence status, execution dispatch refs, evidence refs, threshold snapshots, and policy refs.
- Does not create a new `EvolutionDecision`, redeploy command, rollback command, or cooldown window.

## Acceptance Notes

- Observation report is available for executed decisions.
- Non-executed decisions return `422`.
- Report supports deterministic `as_of` timestamps for replay/audit tests.
- When both observation and cooldown windows have elapsed, the report marks the target as no longer blocked by the decision active window.

## Verification

```bash
python3 -m pytest services/evolution/test_evolution_service.py -q
```

Result: `53 passed in 16.56s`

## Closeout Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/evolution/test_evolution_service.py -q
```

Result: `53 passed in 21.46s`

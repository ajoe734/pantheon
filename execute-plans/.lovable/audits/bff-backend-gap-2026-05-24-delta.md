# BFF Backend Gap Delta Audit - 2026-05-24

Status: task-scoped delta audit

## BFF-MGMT-DELTA-001

Route: `GET /bff/management/persona-league/movers`

Purpose: expose a live BFF Management endpoint for persona-league movement
cards/lists without requiring execute-plans to fan out through raw persona,
ranking, tier, and health routes.

Backend status: implemented.

Frontend client status: typed path and fetch helper added under
`execute-plans/src/lib/bff-v1`.

Notes:

- The route is read-only and uses `policy=read_only_governance_advisory`.
- The BFF does not yet own historical persona-league baseline snapshots.
- Until that source exists, mover items report `baselineStatus=unavailable`,
  `direction=new`, null delta fields, and explicit movement basis.
- `meta.surfaces.persona_league_history` is degraded to make strict fallback
  behavior explicit.

Verification:

```text
git diff --check
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

# Pool Runtime Compatibility Contract

Task: `DEP-004`
Status: implemented

## Purpose

`pool_runtime_compat.py` is the read-only guard between an approved
`DeploymentPlan` and the RuntimeBinding advance path.

It verifies that the capital pool and persona binding can admit the requested
runtime before deployment orchestration emits the RuntimeBinding request. A
failed result must block the advance.

## Public API

```python
check_compatibility(
    capital_pool_id,
    deployment_plan_id,
    *,
    capital_pool=None,
    deployment_plan=None,
    runtime_requirements=None,
    persona_capital_binding=None,
    capital_pool_store=None,
    deployment_plan_store=None,
    persona_capital_binding_store=None,
) -> dict
```

Returned shape:

```json
{
  "capital_pool_id": "pool-001",
  "deployment_plan_id": "plan-001",
  "passed": true,
  "rejection_reasons": [],
  "details": {
    "compatibility_contract": "DEP-004"
  }
}
```

`enforce_compatibility(...)` wraps the same check and raises when `passed` is
false. `services/control-plane/cron/service.py` calls this hook when deploy
payloads include `pool_runtime_compat` / `pool_runtime_compatibility` context,
before execution projection and saga bootstrap.

## Guards

The guard is fail-closed when required proof is present but incompatible.

| Check | Required truth | Rejection reason |
|---|---|---|
| Pool admissibility | `CapitalPool.status == active` | `pool_admissibility_status_not_active` |
| Budget | pool `risk_budget` covers plan `target_size` | `pool_risk_budget_insufficient` |
| Jurisdiction | pool jurisdiction contains runtime broker jurisdiction | `pool_runtime_jurisdiction_mismatch` |
| Runtime mode | runtime mode equals `DeploymentPlan.target_stage` | `runtime_mode_stage_mismatch` |
| Persona binding | binding exists and `status == active` | `persona_capital_binding_missing` / `persona_capital_binding_not_active` |

`risk_budget` is read from `risk_budget`, `risk_budget_amount`,
`max_target_size`, or existing `budget` fields, including metadata aliases.
`target_size` is read from the plan or plan metadata.

## Non-Goals

- Does not write `DeploymentPlan`, `RuntimeBinding`, `CapitalPool`, or binding
  records.
- Does not replace runtime-manager's existing `allowed_deployment_scope` or
  loader checks.
- Does not trigger live broker or capital side effects.

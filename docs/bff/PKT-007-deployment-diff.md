# PKT-007 Governance Deployment Diff BFF Contract

## Purpose

Provide a BFF-composed field-level diff payload for the Governance Deployment Diff screen so the UI does not construct change summaries, risk tiers, or approval gating state client-side.

## Primary Read Route

- `GET /api/v1/operator/deployment-diff/{plan_id}`
- Path parameter: `plan_id` — the deployment plan under review

Required response fields:

- `plan_id`
- `artifact_id`
- `stage`
- `submitted_at`
- `submitted_by`
- `previous_plan_id` (nullable — null means first deployment; no prior plan baseline)
- `changes[]` (list of changed fields; empty when `previous_plan_id` is null and there is no comparison baseline)
  - `field_path` (dot-notation field identifier, e.g. `parameters.max_drawdown`)
  - `previous_value` (null when `previous_plan_id` is null)
  - `current_value`
  - `change_reason` (human-readable description of why this field changed; backend-supplied)
  - `change_category` (`parameters` | `bindings` | `capital_allocation` | `risk_controls` | `stage_transition`)
  - `risk_tier` (`low` | `medium` | `high` | `critical`)
- `change_summary`
  - `total_changes` (integer)
  - `by_category` (map of `change_category` to `{ count, highest_risk_tier }`)
- `allowedActions`
  - `canProceedToApproval` (boolean)
  - `canEscalateDiff` (boolean)
- `meta.snapshot_at`
- `meta.surfaces` (per-surface `status`; must include `deployment_diff`)

## Write Actions

All write actions use `POST /api/v1/operator/commands`.

### Escalate Diff

```json
{
  "command": "EscalateDiff",
  "target": { "type": "DeploymentPlan", "id": "{plan_id}" },
  "action": "escalate_diff",
  "params": { "plan_id": "{plan_id}", "escalation_reason": "required" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

## Design Rules

- The BFF constructs the diff from the current plan and its predecessor. The UI must not compute diff entries from raw plan fields.
- When `previous_plan_id` is null, the `changes[]` array is empty and the response includes a `first_deployment: true` flag. The UI renders a `first deployment — no prior plan baseline` message.
- All CTA-facing fields (`allowedActions.*`) must be backend-shaped.
- When `meta.surfaces.deployment_diff` is `unavailable`, the diff table is replaced with the degraded-state message and the approval CTA is disabled.
- Risk tier annotations are assigned by the BFF based on governance policy. The UI renders the tier labels as supplied; it does not reclassify tiers.
- Inherits `meta.surfaces.*` degradation semantics from `PKT-005 Degradation Banner`.

## Example Payload

- `docs/examples/PKT-007-deployment-diff.json`

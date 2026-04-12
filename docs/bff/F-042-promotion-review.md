# F-042 Promotion Review BFF Contract

## Purpose

Provide a page-shaped payload for the Promotion Review screen so the UI does not need to join deployment, governance, and runtime state client-side.

## Primary Read Route

- `GET /api/v1/operator/deployment-review/{plan_id}`

## Required Fields

- `deployment_plan`
- `approval_decision`
- `capital_pool`
- `bindings`
- `runtime_binding`
- `meta.snapshot_at`
- `meta.surfaces`
- `allowedActions.canPromoteToPaper`
- `latestRun.progress`
- `review.riskSummary`
- `review.governanceOutcome`

## Promote to Paper (Write Action)

The Promotion Review CTA submits a generic operator command:

- `POST /api/v1/operator/commands`
- `command`: `ApproveDeployment`
- `target.type`: `DeploymentPlan`
- `target.id`: `{plan_id}`
- `action`: `approve` or `reject`
- `params` (required):
  - `deployment_plan_id`
  - `approval_decision` (`approve` or `reject`)
- `params` (optional):
  - `verification_notes`
  - `verification_timestamp` (RFC3339)
- `audit_context.reason` is required (operator rationale)

Example payload:

```json
{
  "command": "ApproveDeployment",
  "target": {
    "type": "DeploymentPlan",
    "id": "plan-F-042"
  },
  "action": "approve",
  "params": {
    "deployment_plan_id": "plan-F-042",
    "approval_decision": "approve",
    "verification_notes": "Promotion review approved in UI.",
    "verification_timestamp": "2026-04-12T00:00:00Z"
  },
  "audit_context": {
    "reason": "Promotion review approval.",
    "timestamp": "2026-04-12T00:00:00Z"
  }
}
```

## Design Rules

- Any CTA-facing field must be backend-shaped
- Downstream failure must surface through degradation metadata, never by silently returning empty values
- The UI must not derive promotion safety by itself

## Example Payload

- [F-042-review-page.json](/home/ajoe734/code/pantheon/docs/examples/F-042-review-page.json)

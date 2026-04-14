# PKT-001 Deployment Review Console BFF Contract

## Purpose

Provide page-shaped payloads for the Deployment Review Console so the UI does not need to join deployment plan, approval decision, runtime binding, or governance state client-side.

## Primary Read Routes

### List deployment plans

- `GET /api/v1/operator/deployment-plans`
- Query parameters: `status` (comma-separated: `pending_review`, `approved`, `rejected`), `page_token`, `page_size`

Required response fields per item:

- `plan_id`
- `artifact_id`
- `target_stage`
- `risk_level`
- `governance_outcome`
- `submitted_at`

Required list-level fields (not per-item):

- `page_info.next_page_token` (nullable)
- `meta.snapshot_at` — timestamp of when the BFF snapshotted the list; applies to the whole page, not to individual items
- `meta.surfaces` (per-surface `status`: `ok`, `degraded`, or `unavailable`) — required at the list level so the degradation banner can render without waiting for a detail fetch

### Get deployment plan detail

- `GET /api/v1/operator/deployment-review/{plan_id}`

Required response fields:

- `deployment_plan`
- `approval_decision`
- `capital_pool`
- `bindings`
- `runtime_binding`
- `meta.snapshot_at`
- `meta.surfaces` (per-surface `status`: `ok`, `degraded`, or `unavailable`)
- `allowedActions.canApprove`
- `allowedActions.canReject`
- `allowedActions.canPromoteToPaper`
- `latestRun.progress`
- `review.riskSummary`
- `review.governanceOutcome`

## Write Actions

All write actions use `POST /api/v1/operator/commands`.

### Approve deployment plan

```json
{
  "command": "ApproveDeployment",
  "target": { "type": "DeploymentPlan", "id": "{plan_id}" },
  "action": "approve",
  "params": {
    "deployment_plan_id": "{plan_id}",
    "approval_decision": "approve",
    "verification_notes": "optional operator notes",
    "verification_timestamp": "RFC3339"
  },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Reject deployment plan

```json
{
  "command": "ApproveDeployment",
  "target": { "type": "DeploymentPlan", "id": "{plan_id}" },
  "action": "reject",
  "params": {
    "deployment_plan_id": "{plan_id}",
    "approval_decision": "reject",
    "verification_notes": "required when rejecting",
    "verification_timestamp": "RFC3339"
  },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

## Design Rules

- All CTA-facing fields must be backend-shaped in `allowedActions`.
- The UI must not compute approval eligibility or risk classification locally.
- When any surface in `meta.surfaces` is `degraded` or `unavailable`, the BFF must include a `degradation` object describing which surfaces are affected and whether CTAs should be disabled.
- Downstream failure must surface through degradation metadata, never by silently returning empty values.

## Example Payload

- `docs/examples/PKT-001-deployment-review-console.json`

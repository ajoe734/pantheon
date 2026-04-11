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

## Design Rules

- Any CTA-facing field must be backend-shaped
- Downstream failure must surface through degradation metadata, never by silently returning empty values
- The UI must not derive promotion safety by itself

## Example Payload

- [F-042-review-page.json](/home/ajoe734/code/pantheon/docs/examples/F-042-review-page.json)

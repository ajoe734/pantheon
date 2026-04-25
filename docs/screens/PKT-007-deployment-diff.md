# PKT-007 Governance Deployment Diff

## Classification

- Workbench: Governance Workbench
- Screen ID: `screen-governance-deployment-diff`
- Feature ID: `PKT-007-deployment-diff`
- Packet status: ready

## User Goal

Give a governance operator a structured field-level diff between the current deployment plan and its predecessor so they can evaluate what changed, assess risk tier annotations, and proceed to approval or escalation without reconstructing deployment state in the browser.

## Page Sections

- **Plan identity header**: shows `plan_id`, `artifact_id`, `stage`, `submitted_at`, `submitted_by`, and comparison baseline (`previous_plan_id` or `no prior plan`).
- **Diff summary rail**: grouped change counts by `change_category` (`parameters`, `bindings`, `capital_allocation`, `risk_controls`, `stage_transition`). Each category shows an aggregate change count and highest risk tier annotation.
- **Field diff table**: one row per changed field with `field_path`, `previous_value`, `current_value`, `change_reason`, and `risk_tier` (`low` | `medium` | `high` | `critical`). Unchanged fields are not shown.
- **Risk tier legend**: non-interactive legend mapping risk tier labels to their governance policy meaning.
- **Approval gating panel**: shows `allowedActions.canProceedToApproval` and `allowedActions.canEscalateDiff`. CTA visibility is backend-shaped only.
- **Degraded-diff state**: when `meta.surfaces.deployment_diff` is `unavailable`, the diff table is replaced with the degraded-state message and the approval CTA is disabled.
- **Degradation banner**: when any BFF surface is degraded, a non-dismissable banner is shown.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/deployment-diff/{plan_id}`.
- The UI does not construct the diff client-side from raw plan fields. All diff entries come from the BFF response.
- When `previous_plan_id` is null, the diff panel shows a `first deployment — no prior plan baseline` message rather than an empty diff table.
- CTA visibility comes from `allowedActions` in the BFF response only.
- Escalation uses `POST /api/v1/operator/commands` with `EscalateDiff`.
- If `allowedActions` fields are missing, the UI must emit a `bff-gap` handoff.
- Inherits `meta.staleness` and `meta.surfaces.*` degradation semantics from `PKT-005`.

## Acceptance

- Diff table renders from BFF-supplied `changes[]` entries only; no client-side diff computation.
- `previous_plan_id` null case shows an explicit `first deployment` message, not an empty table.
- Risk tier annotation appears on each changed field row.
- Approval gating CTA (`canProceedToApproval`) is hidden when the field is absent or false.
- Degraded diff surface suppresses the diff table and disables approval CTA.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if `allowedActions` fields are missing.

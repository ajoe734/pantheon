# PKT-001 Deployment Review Console

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-deployment-review`
- Feature ID: `PKT-001-deployment-review`
- Packet status: ready

## User Goal

Give an operator a single surface to browse pending deployment plans, inspect each plan's review state and risk profile, and submit approval or rejection decisions without reconstructing governance or runtime state in the browser.

## Page Sections

- **Deployment Plan List panel**: paginated list of deployment plans filtered by status (`pending_review`, `approved`, `rejected`). Each row shows `plan_id`, `artifact_id`, `target_stage`, `risk_level`, `governance_outcome`, and `submitted_at`.
- **Deployment Plan Detail panel**: opens on row selection. Shows full review snapshot including `approval_decision`, `bindings`, `capital_pool`, `runtime_binding`, `latestRun.progress`, and `review.riskSummary`.
- **Allowed Actions panel**: CTA visibility (`canApprove`, `canReject`, `canPromoteToPaper`) is backend-shaped only. No local eligibility logic.
- **Degradation banner**: when any BFF surface has `status != "ok"`, a non-dismissable banner explains which surfaces are degraded and disables the affected CTAs.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from Pantheon BFF routes only.
- CTA visibility and enabled/disabled state comes from `allowedActions` fields in the BFF response.
- Filtering and sorting parameters are passed as query parameters to `GET /api/v1/operator/deployment-plans`; the UI does not filter or sort client-side.
- If a required field is absent from the BFF response, the UI must emit a `bff-gap` handoff instead of inventing local state.
- Write actions use `POST /api/v1/operator/commands` only. No direct route mutations.

## Acceptance

- List panel renders with real BFF data and no mock rows.
- Detail panel opens from list row selection and renders all required fields.
- `Approve`, `Reject`, and `Promote to paper` CTA visibility is backend-driven only.
- Degraded surfaces disable the relevant CTAs and display the degradation banner.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any `allowedActions` field is missing.

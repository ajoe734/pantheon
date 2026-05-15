# PKT-006 Governance Approval Queue

## Classification

- Workbench: Governance Workbench
- Screen ID: `screen-governance-approval-queue`
- Feature ID: `PKT-006-approval-queue`
- Packet status: ready

## User Goal

Give a governance operator a queue-based view of all approval decisions pending action so they can inspect each decision in context, approve or reject it, and complete the governance chain without reconstructing deployment or approval state in the browser.

## Page Sections

- **Approval Queue list**: paginated list of pending approval decisions. Each row shows `decision_id`, `decision_type` (`DeploymentPlan` | `EvolutionProposal` | `PersonaBinding`), `risk_level`, `submitted_at`, `submitted_by`, `decision_state`, and `allowedActions.canApprove` / `allowedActions.canReject`.
- **Decision Detail drawer**: opens on row selection. Shows `decision_context`, `risk_summary`, `evidence_refs`, `governance_chain` (upstream review reference), `decision_state`, and `required_approvals`.
- **Approval Actions panel**: CTA visibility (`canApprove`, `canReject`, `canRequestRevision`) is backend-shaped only.
- **Filter rail**: filter by `decision_type`, `risk_level`, and `decision_state`. Filters are passed as query parameters; no client-side filtering.
- **Degradation banner**: when any BFF surface is degraded, a non-dismissable banner disables approval CTAs.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/governance/approval-queue`.
- CTA visibility comes from `allowedActions` fields in the BFF response.
- Filter and pagination parameters are sent as query params to the BFF. No client-side filtering.
- Approval and rejection actions use `POST /api/v1/operator/commands`. No direct mutations.
- If `allowedActions` fields are missing, the UI must emit a `bff-gap` handoff.
- Inherits the queue model, pagination contract, and `allowedActions` pattern from `GV-01 Review Queue (PKT-001)`.

## Acceptance

- Queue list renders with real BFF data and no mock rows.
- Detail drawer opens from list row selection and renders all required fields.
- `Approve` and `Reject` CTA visibility is backend-driven only.
- `canReject` and `canApprove` are never both absent for a `pending` decision; if both are missing, emit the `bff-gap` handoff.
- Degraded surfaces disable approval CTAs and display the degradation banner.
- Filters pass through to the BFF query — no client-side implementation of filter logic.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if `allowedActions` fields are missing.

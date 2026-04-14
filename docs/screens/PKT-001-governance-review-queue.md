# PKT-001 Governance Review Queue

## Classification

- Workbench: Governance Workbench
- Screen ID: `screen-governance-review-queue`
- Feature ID: `PKT-001-governance-review-queue`
- Packet status: ready

## User Goal

Give a governance operator a queue-based view of all items pending review so they can prioritize, inspect, and route each item to the approval or rejection path without reconstructing governance state in the browser.

## Page Sections

- **Review Queue list**: paginated list of pending review items. Each row shows `item_id`, `item_type` (`DeploymentPlan`, `EvolutionProposal`, `PersonaBinding`), `risk_level`, `submitted_at`, `submitted_by`, `governance_outcome` (if already decided), and `allowedActions.canReview`.
- **Item Detail drawer**: opens on row selection. Shows `review_summary`, `risk_assessment`, `governance_outcome`, `approval_decision` (if linked), and supporting evidence refs.
- **Routing Actions panel**: CTA visibility (`canForwardToApproval`, `canRequestChanges`, `canEscalate`) is backend-shaped only.
- **Filter rail**: filter by `item_type`, `risk_level`, and `status`. Filters are passed as query parameters; no client-side filtering.
- **Degradation banner**: when any BFF surface is degraded, a non-dismissable banner disables routing CTAs.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/governance/review-queue`.
- CTA visibility comes from `allowedActions` fields in the BFF response.
- Filter and pagination parameters are sent as query params to the BFF. No client-side filtering.
- Routing actions use `POST /api/v1/operator/commands`. No direct mutations.
- If `allowedActions` fields are missing, the UI must emit a `bff-gap` handoff.

## Acceptance

- Queue list renders with real BFF data and no mock rows.
- Detail drawer opens from list row selection and renders all required fields.
- `Forward to Approval`, `Request Changes`, and `Escalate` CTA visibility is backend-driven only.
- Degraded surfaces disable routing CTAs and display the degradation banner.
- Filters pass through to the BFF query — no client-side implementation of filter logic.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if `allowedActions` fields are missing.

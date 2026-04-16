# PKT-004 Module C — Deployment / Approval Drilldowns

## Classification

- Workbench: Persona Workbench (shared with Governance Workbench)
- Module: `Module C — Deployment / Approval Drilldowns`
- Feature ID: `PKT-004-deployment-approval-drilldowns`
- Packet status: ready (shared drilldown reference — do not fork PKT-001 governance contracts)

## User Goal

Allow an operator to browse deployment plans and approval decisions as read-only drilldown context linked from persona or binding journeys. This module defines the persona-side drilldown entry points only; authoritative governance review and approval-queue interactions are owned by PKT-001.

## Surface Inventory

| Surface | Screen ID | Endpoint | User action |
|---|---|---|---|
| `DP-01` Deployment Plan List | `screen-deployment-plan-list` | `GET /api/v1/deployment-plans` | Browse deployment plans with status and artifact refs |
| `DP-02` Deployment Plan Detail | `screen-deployment-plan-detail` | `GET /api/v1/deployment-plans/{plan_id}` | Inspect one plan with approval decision embedded |
| `DP-03` Approval Decision List | `screen-approval-decision-list` | `GET /api/v1/approval-decisions` | Browse approval decisions with outcome and risk level |
| `DP-04` Approval Decision Detail | `screen-approval-decision-detail` | `GET /api/v1/approval-decisions/{decision_id}` | Inspect one approval decision |

## Shared Governance Boundary

These four surfaces are **read-only drilldown references** from persona and binding context. They do NOT define:

- Governance review queue layout or routing actions → owned by `PKT-001 Governance Review Queue`
- Deployment plan approval or rejection flows → owned by `PKT-001 Deployment Review Console`
- `allowedActions.canApprove` / `canReject` authority → owned by `PKT-001`

Any screen showing DP-01..DP-04 surfaces in a persona journey must link to the PKT-001 governance screens for action workflows and must not duplicate the PKT-001 CTA or approval decision logic.

## Page Sections Per Surface

### DP-01 Deployment Plan List

- **Plan list**: rows showing `id`, `artifact_id`, `artifact_version`, `target_stage`, `status`, `transition_type`, `capital_pool_id`.
- **Filter rail**: filter by `status`, `capital_pool_id`. Filters passed as query parameters.
- Clicking a row navigates to `DP-02 Deployment Plan Detail`.

### DP-02 Deployment Plan Detail

- **Plan identity block**: `id`, `artifact_id`, `artifact_version`, `target_stage`, `current_stage`, `status`, `transition_type`.
- **Approval decision summary**: embedded `approval_decision` with `outcome`, `state`, `risk_level`, `decided_at`.
- **Cross-workbench link**: "View in Governance Console" link to `PKT-001 Deployment Review Console` for the same `plan_id`.

### DP-03 Approval Decision List

- **Decision list**: rows showing `id`, `outcome`, `state`, `reviewer`, `decided_at`, `risk_level`.
- **Filter rail**: filter by `outcome`, `state`. Filters passed as query parameters.
- Clicking a row navigates to `DP-04 Approval Decision Detail`.

### DP-04 Approval Decision Detail

- **Decision identity block**: `id`, `outcome`, `state`, `reviewer`, `decided_at`, `risk_level`.
- **Cross-workbench link**: "View in Governance Console" link to `PKT-001 Governance Review Queue` for the related item.

## Interaction Rules

- All data comes from BFF read routes listed above.
- No write or command actions are defined in this module. All governance commands route through PKT-001.
- Filters passed as query parameters — no client-side filtering.
- `viewer` role tokens are rejected.

## Non-Blocking BFF Caveats

- `viewer` role is rejected on all DP surfaces.
- `meta.staleness` on list surfaces may be non-null.

## Acceptance

- All four DP surfaces render with real BFF data and no mock rows.
- No governance CTA or approval command logic is re-implemented; cross-links to PKT-001 screens are used instead.
- Filter parameters pass through to the BFF.
- Loading, empty, and error states are explicit and visually distinct.

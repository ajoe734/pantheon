# PKT-008 Governance Rollback Review

## Classification

- Workbench: Governance Workbench
- Screen ID: `screen-governance-rollback-review`
- Feature ID: `PKT-008-rollback-review`
- Packet status: ready

## User Goal

Give a governance operator a complete picture of a pending rollback request — scope, position impact, affected bindings, trigger reason, and approval authority — so they can approve or reject the rollback safely without reconstructing position or deployment state in the browser.

## Page Sections

- **Rollback identity header**: shows `rollback_id`, `target_plan_id`, `trigger_reason`, `requested_at`, `requested_by`, and `rollback_scope` (`full` | `partial`).
- **Scope summary**: shows the number of affected personas, affected bindings, and target deployment stage.
- **Position impact table**: one row per affected binding with `binding_id`, `persona_id`, `current_stage`, `target_stage`, and `position_impact_summary`. When position data is stale, each row shows a `position_data_stale` badge and the impact summary is replaced with a stale-data message.
- **Affected bindings panel**: lists `binding_id`, `persona_id`, `capital_pool_id`, and `current_stage` for all bindings in scope.
- **Trigger evidence drawer**: shows `trigger_reason`, `evidence_refs[]`, and `linked_incident_id` if present.
- **Approval actions panel**: shows `allowedActions.canApproveRollback` and `allowedActions.canRejectRollback`. CTA visibility is backend-shaped only.
- **Stale position data warning**: when `meta.surfaces.position_data` is `degraded` or `unavailable`, the approval CTA is disabled and a warning message explains why.
- **Degradation banner**: when any BFF surface is degraded, a non-dismissable banner is shown.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/rollback-review/{rollback_id}`.
- The UI does not derive position impact from raw binding or telemetry data. All position impact fields come from the BFF response.
- `allowedActions.canApproveRollback` must be `true` before the Approve CTA is rendered.
- When `meta.surfaces.position_data` is `degraded` or `unavailable`, the Approve CTA is disabled regardless of `allowedActions`.
- Approval and rejection use `POST /api/v1/operator/commands` with `ApproveRollback` or `RejectRollback`.
- If `allowedActions` fields are missing, the UI must emit a `bff-gap` handoff.
- Inherits rollback action semantics from `ROLLBACK_AND_POSITION_SEMANTICS.md`; does not redefine what constitutes a safe rollback target.

## Acceptance

- Rollback scope, position impact, and affected bindings render from BFF-supplied fields only.
- Stale position data shows the stale badge and suppresses the Approve CTA.
- `canApproveRollback` and `canRejectRollback` absent in the BFF response triggers `bff-gap` handoff.
- Degraded position data surface disables the Approve CTA even when `allowedActions.canApproveRollback` is true.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Evidence drawer opens on user interaction; trigger reason and evidence refs render from BFF fields only.

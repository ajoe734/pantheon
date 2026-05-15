# PKT-009 Governance Audit Rail BFF Contract

## Purpose

Provide a filterable, paginated governance audit trail payload so the UI does not reconstruct actor authority, action labels, or evidence linkages client-side.

## Primary Read Route

- `GET /api/v1/operator/governance/audit`
- Query parameters:
  - `actor` (optional string — filter by actor identity)
  - `action_type` (optional comma-separated: `ApproveDecision`, `RejectDecision`, `ApproveRollback`, `RejectRollback`, `EscalateDiff`, `ForwardToApprovalQueue`, `RequestGovernanceChanges`, `EscalateGovernanceItem`, `RequestApprovalRevision`, `ApproveDeployment`, `RejectDeployment`)
  - `target_type` (optional: `DeploymentPlan` | `ApprovalDecision` | `Rollback` | `GovernanceReviewItem`)
  - `from` (optional RFC3339 start of date range)
  - `to` (optional RFC3339 end of date range)
  - `page_token`, `page_size`

Required response fields:

- `entries[]`
  - `entry_id`
  - `actor`
  - `action_type`
  - `target_type`
  - `target_id`
  - `timestamp` (RFC3339)
  - `outcome` (`success` | `rejected` | `escalated`)
  - `audit_context`
    - `reason` (operator-supplied rationale; null when not provided)
  - `evidence_refs[]` (each with `ref_id`, `type`, `url`; may be empty)
- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`
- `meta.surfaces` (per-surface `status`; must include `audit_trail`)

## Write Actions

The audit rail is read-only. No write actions originate from this screen.

## Design Rules

- The audit trail is append-only and read-only from the front-end perspective.
- All filter parameters are applied by the BFF. The UI must not perform client-side filtering or sorting of entries.
- When `meta.surfaces.audit_trail` is `degraded`, the response includes any available entries. The UI renders the delayed-data banner alongside available entries in read-only mode.
- When `meta.surfaces.audit_trail` is `unavailable`, the `entries[]` array will be empty. The UI renders the unavailable-data message rather than an empty list.
- Actor and action type labels are supplied by the BFF and rendered as-is. The UI must not invent display labels.
- `evidence_refs[]` may be empty for actions that carry no evidence. An empty array is not an error state.
- Inherits `meta.surfaces.*` degradation semantics from `PKT-005 Degradation Banner`.

## Example Payload

- `docs/examples/PKT-009-governance-audit-rail.json`

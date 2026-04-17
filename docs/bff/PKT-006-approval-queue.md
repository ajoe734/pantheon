# PKT-006 Governance Approval Queue BFF Contract

## Purpose

Provide a page-shaped queue payload for the Governance Approval Queue screen so the UI does not need to join approval decisions, risk classifications, or governance authority state client-side.

## Primary Read Route

- `GET /api/v1/operator/governance/approval-queue`
- Query parameters: `decision_type` (comma-separated: `DeploymentPlan`, `EvolutionProposal`, `PersonaBinding`), `risk_level` (comma-separated: `low`, `medium`, `high`, `critical`), `decision_state` (comma-separated: `pending`, `in_review`, `escalated`), `page_token`, `page_size`

Required response fields:

- `items[]`
  - `decision_id`
  - `decision_type` (`DeploymentPlan` | `EvolutionProposal` | `PersonaBinding`)
  - `risk_level` (`low` | `medium` | `high` | `critical`)
  - `submitted_at`
  - `submitted_by`
  - `decision_state` (`pending` | `in_review` | `approved` | `rejected` | `escalated`)
  - `allowedActions.canApprove`
  - `allowedActions.canReject`
  - `allowedActions.canRequestRevision`
  - `decision_context` (embedded per item; powers the detail drawer without a separate fetch)
    - `risk_summary`
    - `evidence_refs[]` (each with `ref_id`, `type`, `url`)
    - `governance_chain` (upstream review item ref; `linked_review_item_id` nullable)
    - `required_approvals` (count of required approvals for the decision type)
- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`
- `meta.surfaces` (per-surface `status`)

## Write Actions

All write actions use `POST /api/v1/operator/commands`.

### Approve Decision

```json
{
  "command": "ApproveDecision",
  "target": { "type": "ApprovalDecision", "id": "{decision_id}" },
  "action": "approve",
  "params": { "decision_id": "{decision_id}", "approval_notes": "optional" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Reject Decision

```json
{
  "command": "RejectDecision",
  "target": { "type": "ApprovalDecision", "id": "{decision_id}" },
  "action": "reject",
  "params": { "decision_id": "{decision_id}", "rejection_reason": "required" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Request Revision

```json
{
  "command": "RequestApprovalRevision",
  "target": { "type": "ApprovalDecision", "id": "{decision_id}" },
  "action": "request_revision",
  "params": { "decision_id": "{decision_id}", "revision_notes": "required" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

## Design Rules

- All CTA-facing fields (`allowedActions.*`) must be backend-shaped.
- The UI must not derive approval authority or decision eligibility locally.
- Filters are sent as query parameters; the BFF applies them. No client-side filtering.
- When any surface in `meta.surfaces` is `degraded` or `unavailable`, approval CTAs must be disabled and the degradation banner must appear.
- The `decision_context` sub-object is embedded per item so the detail drawer requires no additional fetch.
- This route extends the `/api/v1/approval-decisions` primitives with a governance-queue projection; it does not replace the underlying decision resource.
- Inherits the queue model and `allowedActions` pattern established by `PKT-001 Governance Review Queue`.

## Example Payload

- `docs/examples/PKT-006-approval-queue.json`

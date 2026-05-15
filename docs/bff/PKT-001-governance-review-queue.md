# PKT-001 Governance Review Queue BFF Contract

## Purpose

Provide a page-shaped queue payload for the Governance Review Queue screen so the UI does not need to join review items, risk classifications, or governance authority state client-side.

## Primary Read Route

- `GET /api/v1/operator/governance/review-queue`
- Query parameters: `item_type` (comma-separated: `DeploymentPlan`, `EvolutionProposal`, `PersonaBinding`), `risk_level` (comma-separated: `low`, `medium`, `high`, `critical`), `status` (comma-separated: `pending`, `in_review`, `escalated`), `page_token`, `page_size`

Required response fields:

- `items[]`
  - `item_id`
  - `item_type` (`DeploymentPlan` | `EvolutionProposal` | `PersonaBinding`)
  - `risk_level` (`low` | `medium` | `high` | `critical`)
  - `submitted_at`
  - `submitted_by`
  - `governance_outcome` (`pending` | `approved` | `rejected` | `escalated`)
  - `allowedActions.canReview`
  - `allowedActions.canForwardToApproval`
  - `allowedActions.canRequestChanges`
  - `allowedActions.canEscalate`
  - `review_summary` (embedded per item; powers the detail drawer without a separate fetch)
    - `risk_assessment`
    - `evidence_refs[]`
    - `linked_approval_decision_id` (nullable)
- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`
- `meta.surfaces` (per-surface `status`)

## Write Actions

All write actions use `POST /api/v1/operator/commands`.

### Forward to Approval Queue

```json
{
  "command": "ForwardToApprovalQueue",
  "target": { "type": "GovernanceReviewItem", "id": "{item_id}" },
  "action": "forward",
  "params": { "item_id": "{item_id}", "reviewer_notes": "optional" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Request Changes

```json
{
  "command": "RequestGovernanceChanges",
  "target": { "type": "GovernanceReviewItem", "id": "{item_id}" },
  "action": "request_changes",
  "params": { "item_id": "{item_id}", "change_summary": "required" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Escalate

```json
{
  "command": "EscalateGovernanceItem",
  "target": { "type": "GovernanceReviewItem", "id": "{item_id}" },
  "action": "escalate",
  "params": { "item_id": "{item_id}", "escalation_reason": "required" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

## Design Rules

- All CTA-facing fields (`allowedActions.*`) must be backend-shaped.
- The UI must not derive governance authority or routing eligibility locally.
- Filters are sent as query parameters; the BFF applies them. No client-side filtering.
- When any surface in `meta.surfaces` is `degraded` or `unavailable`, routing CTAs must be disabled and the degradation banner must appear.

## Example Payload

- `docs/examples/PKT-001-governance-review-queue.json`

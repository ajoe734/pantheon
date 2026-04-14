# PKT-001 Governance Review Queue — Frontend Change Spec

## Feature

- Feature ID: `PKT-001-governance-review-queue`
- Screen ID: `screen-governance-review-queue`
- Workbench: Governance Workbench
- Packet status: ready

## Summary

Build the **Governance Review Queue** inside `front-ai-trading-system`. This screen gives governance operators a queue-based view of all items pending review so they can prioritize, inspect, and route each item to the approval or rejection path. All data and CTA authority must come from Pantheon BFF — no local derivation or client-side filtering.

## Files to Create or Modify

```
src/pages/governance/GovernanceReviewQueue.tsx   — new queue list page
src/pages/governance/ReviewItemDetail.tsx         — new item detail drawer
src/pages/governance/types.ts                     — add governance review queue types
src/lib/bffClient.ts                              — add governance-review-queue fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch review queue

```
GET /api/v1/operator/governance/review-queue
Query params: item_type (comma-separated: DeploymentPlan | EvolutionProposal | PersonaBinding),
              risk_level (comma-separated: low | medium | high | critical),
              status (comma-separated: pending | in_review | escalated),
              page_token, page_size
```

Expected response shape (see `docs/examples/PKT-001-governance-review-queue.json` for a full example):

```typescript
interface GovernanceReviewQueueResponse {
  items: GovernanceReviewItem[];
  page_info: { next_page_token: string | null };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, "ok" | "degraded" | "unavailable">;
  };
}
interface GovernanceReviewItem {
  item_id: string;
  item_type: "DeploymentPlan" | "EvolutionProposal" | "PersonaBinding";
  risk_level: "low" | "medium" | "high" | "critical";
  submitted_at: string;
  submitted_by: string;
  governance_outcome: "pending" | "approved" | "rejected" | "escalated";
  allowedActions: {
    canReview: boolean;
    canForwardToApproval: boolean;
    canRequestChanges: boolean;
    canEscalate: boolean;
  };
  review_summary?: {
    risk_assessment: string;
    evidence_refs: string[];
    linked_approval_decision_id: string | null;
  };
}
```

### Submit routing action

```
POST /api/v1/operator/commands
```

Forward to Approval Queue:
```json
{
  "command": "ForwardToApprovalQueue",
  "target": { "type": "GovernanceReviewItem", "id": "{item_id}" },
  "action": "forward",
  "params": { "item_id": "{item_id}", "reviewer_notes": "" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

Request Changes:
```json
{
  "command": "RequestGovernanceChanges",
  "target": { "type": "GovernanceReviewItem", "id": "{item_id}" },
  "action": "request_changes",
  "params": { "item_id": "{item_id}", "change_summary": "<required>" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

Escalate:
```json
{
  "command": "EscalateGovernanceItem",
  "target": { "type": "GovernanceReviewItem", "id": "{item_id}" },
  "action": "escalate",
  "params": { "item_id": "{item_id}", "escalation_reason": "<required>" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

## Component Structure

### `GovernanceReviewQueue.tsx`

- Renders the paginated queue list.
- Fetches from `GET /api/v1/operator/governance/review-queue` on mount and on filter change.
- Renders one row per item using the `GovernanceReviewItem` shape.
- Provides filter rail for `item_type`, `risk_level`, and `status` — all filters passed as query params to the BFF. No client-side filtering.
- Clicking a row opens the `ReviewItemDetail` drawer.
- Shows the degradation banner when any `meta.surfaces` entry is `"degraded"` or `"unavailable"`.
- Renders loading, empty, degraded, and error states as distinct visual states.

### `ReviewItemDetail.tsx`

- Receives `item_id` (and the already-fetched `GovernanceReviewItem`) as props.
- Renders `review_summary`, `risk_assessment`, `governance_outcome`, `linked_approval_decision_id`, and `evidence_refs`.
- Renders Forward to Approval, Request Changes, and Escalate CTAs only when the corresponding `allowedActions` field is `true`.
- Disables all routing CTAs and shows the degradation banner when any `meta.surfaces` entry is degraded or unavailable.
- On CTA click, calls `POST /api/v1/operator/commands` with the appropriate payload.
- Renders loading, error, and degraded states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- CTA visibility (`canForwardToApproval`, `canRequestChanges`, `canEscalate`) must come from `allowedActions` in the BFF response. Do not derive routing authority locally.
- If a required `allowedActions` field is absent from the BFF response, write `.coordination/requests/PKT-001-governance-review-queue-bff-gap.yaml` using `.coordination/requests/PKT-001-governance-review-queue-bff-gap.example.yaml` as the template and stop implementation.
- Do not invent fields or supplement the BFF response with client-derived values.
- All filter selections must be sent as query parameters to `GET /api/v1/operator/governance/review-queue`. Do not implement any client-side filtering.

## Degradation Handling

When `meta.surfaces` contains any entry with status `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.
- Disable all routing CTAs (Forward, Request Changes, Escalate).
- Do not hide the queue content — show it read-only with the banner visible.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-001-governance-review-queue-ui-done.yaml` using `.coordination/requests/PKT-001-governance-review-queue-ui-done.example.yaml` as the template. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step automatically.

## References

- BFF contract: `docs/bff/PKT-001-governance-review-queue.md`
- Screen spec: `docs/screens/PKT-001-governance-review-queue.md`
- Example payload: `docs/examples/PKT-001-governance-review-queue.json`
- Contract-ready: `.coordination/responses/PKT-001-governance-review-queue-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-001-governance-review-queue-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-001-governance-review-queue-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-001-governance-review-queue-ui-done.example.yaml`

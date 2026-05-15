# PKT-006 Governance Approval Queue — Frontend Change Spec

## Feature

- Feature ID: `PKT-006-approval-queue`
- Screen ID: `screen-governance-approval-queue`
- Workbench: Governance Workbench
- Packet status: ready

## Summary

Build the **Governance Approval Queue** inside `front-ai-trading-system`. This screen gives governance operators a queue-based view of all approval decisions pending action so they can inspect each decision in context, approve or reject it, and complete the governance chain. All data and CTA authority must come from Pantheon BFF — no local derivation or client-side filtering.

## Files to Create or Modify

```
src/pages/governance/GovernanceApprovalQueue.tsx   — new approval queue list page
src/pages/governance/ApprovalDecisionDetail.tsx    — new decision detail drawer
src/pages/governance/types.ts                       — add approval queue types
src/lib/bffClient.ts                                — add approval-queue fetch and command helpers
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch approval queue

```
GET /api/v1/operator/governance/approval-queue
Query params: decision_type (comma-separated: DeploymentPlan | EvolutionProposal | PersonaBinding),
              risk_level (comma-separated: low | medium | high | critical),
              decision_state (comma-separated: pending | in_review | escalated),
              page_token, page_size
```

Expected response shape (see `docs/examples/PKT-006-approval-queue.json` for a full example):

```typescript
interface GovernanceApprovalQueueResponse {
  items: ApprovalQueueItem[];
  page_info: { next_page_token: string | null };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
  };
}
interface ApprovalQueueItem {
  decision_id: string;
  decision_type: "DeploymentPlan" | "EvolutionProposal" | "PersonaBinding";
  risk_level: "low" | "medium" | "high" | "critical";
  submitted_at: string;
  submitted_by: string;
  decision_state: "pending" | "in_review" | "approved" | "rejected" | "escalated";
  allowedActions: {
    canApprove: boolean;
    canReject: boolean;
    canRequestRevision: boolean;
  };
  decision_context: {
    risk_summary: string;
    evidence_refs: Array<{ ref_id: string; type: string; url: string | null }>;
    governance_chain: { linked_review_item_id: string | null };
    required_approvals: number;
  };
}
```

### Approve decision

```
POST /api/v1/operator/commands
```

```json
{
  "command": "ApproveDecision",
  "target": { "type": "ApprovalDecision", "id": "{decision_id}" },
  "action": "approve",
  "params": { "decision_id": "{decision_id}", "approval_notes": "" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

### Reject decision

```json
{
  "command": "RejectDecision",
  "target": { "type": "ApprovalDecision", "id": "{decision_id}" },
  "action": "reject",
  "params": { "decision_id": "{decision_id}", "rejection_reason": "<required>" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

### Request revision

```json
{
  "command": "RequestApprovalRevision",
  "target": { "type": "ApprovalDecision", "id": "{decision_id}" },
  "action": "request_revision",
  "params": { "decision_id": "{decision_id}", "revision_notes": "<required>" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

## Component Structure

### `GovernanceApprovalQueue.tsx`

- Renders the paginated queue list.
- Fetches from `GET /api/v1/operator/governance/approval-queue` on mount and on filter change.
- Renders one row per item using the `ApprovalQueueItem` shape.
- Provides filter rail for `decision_type`, `risk_level`, and `decision_state` — all filters passed as query params to the BFF. No client-side filtering.
- Clicking a row opens the `ApprovalDecisionDetail` drawer.
- Shows the degradation banner when any `meta.surfaces` entry is `"degraded"` or `"unavailable"`.
- Renders loading, empty, degraded, and error states as distinct visual states.

### `ApprovalDecisionDetail.tsx`

- Receives `decision_id` (and the already-fetched `ApprovalQueueItem`) as props.
- Renders `decision_context.risk_summary`, `decision_context.evidence_refs`, `decision_context.governance_chain`, `decision_context.required_approvals`, and `decision_state`.
- Renders Approve, Reject, and Request Revision CTAs only when the corresponding `allowedActions` field is `true`.
- Disables all CTAs and shows the degradation banner when any `meta.surfaces` entry is degraded or unavailable.
- On CTA click, calls `POST /api/v1/operator/commands` with the appropriate payload.
- Renders loading, error, and degraded states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- CTA visibility (`canApprove`, `canReject`, `canRequestRevision`) must come from `allowedActions` in the BFF response. Do not derive approval authority locally.
- If a required `allowedActions` field is absent from the BFF response, write `.coordination/requests/PKT-006-approval-queue-bff-gap.yaml` and stop implementation.
- Do not invent fields or supplement the BFF response with client-derived values.
- All filter selections must be sent as query parameters. Do not implement any client-side filtering.
- The queue model and pagination pattern should match `PKT-001 Governance Review Queue`; do not fork the queue UI pattern.

## Degradation Handling

When `meta.surfaces` contains any entry with status `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.
- Disable all approval CTAs (Approve, Reject, Request Revision).
- Do not hide the queue content — show it read-only with the banner visible.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-006-approval-queue-ui-done.yaml`. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step.

## References

- BFF contract: `docs/bff/PKT-006-approval-queue.md`
- Screen spec: `docs/screens/PKT-006-approval-queue.md`
- Example payload: `docs/examples/PKT-006-approval-queue.json`
- Contract-ready: `.coordination/responses/PKT-006-approval-queue-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-006-approval-queue-lovable-ui-task.yaml`
- Baseline queue pattern: `docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md`

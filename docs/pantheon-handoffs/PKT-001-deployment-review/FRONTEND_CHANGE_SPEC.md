# PKT-001 Deployment Review Console — Frontend Change Spec

## Feature

- Feature ID: `PKT-001-deployment-review`
- Screen ID: `screen-operator-deployment-review`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Deployment Review Console** inside `front-ai-trading-system`. This screen gives operators a single surface to browse pending deployment plans, inspect each plan's review state and risk profile, and submit approval or rejection decisions. All data and CTA authority must come from Pantheon BFF — no local derivation.

## Files to Create or Modify

```
src/pages/operator/DeploymentReviewConsole.tsx   — new list panel page
src/pages/operator/DeploymentPlanDetail.tsx       — new detail panel page
src/pages/operator/types.ts                       — add deployment-review types
src/lib/bffClient.ts                              — add deployment-review fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch deployment plan list

```
GET /api/v1/operator/deployment-plans
Query params: status (comma-separated: pending_review | approved | rejected), page_token, page_size
```

Expected response shape (see `docs/examples/PKT-001-deployment-review-console.json` for a full example):

```typescript
interface DeploymentPlanListResponse {
  list: {
    items: DeploymentPlanSummary[];
    page_info: { next_page_token: string | null };
    meta: { snapshot_at: string };
  };
}
interface DeploymentPlanSummary {
  plan_id: string;
  artifact_id: string;
  target_stage: string;
  risk_level: "low" | "medium" | "high" | "critical";
  governance_outcome: "pending" | "approved" | "rejected" | "escalated";
  submitted_at: string;
}
```

### Fetch deployment plan detail

```
GET /api/v1/operator/deployment-review/{plan_id}
```

Expected response shape (see example payload for complete structure):

```typescript
interface DeploymentPlanDetailResponse {
  deployment_plan: object;
  approval_decision: object;
  capital_pool: object;
  bindings: object[];
  runtime_binding: object;
  latestRun: { progress: number | null };
  review: { riskSummary: string; governanceOutcome: string };
  allowedActions: {
    canApprove: boolean;
    canReject: boolean;
    canPromoteToPaper: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, "ok" | "degraded" | "unavailable">;
  };
}
```

### Submit action

```
POST /api/v1/operator/commands
```

Approve payload:
```json
{
  "command": "ApproveDeployment",
  "target": { "type": "DeploymentPlan", "id": "{plan_id}" },
  "action": "approve",
  "params": {
    "deployment_plan_id": "{plan_id}",
    "approval_decision": "approve",
    "verification_notes": "",
    "verification_timestamp": "<RFC3339>"
  },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

Reject payload: same structure with `"action": "reject"` and `"approval_decision": "reject"`. `verification_notes` is required when rejecting.

## Component Structure

### `DeploymentReviewConsole.tsx`

- Renders the list panel.
- Fetches from `GET /api/v1/operator/deployment-plans` on mount and on filter change.
- Renders one row per item using the `DeploymentPlanSummary` shape.
- Supports status filter passed as a query param — no client-side filter logic.
- Clicking a row opens the detail panel (`DeploymentPlanDetail`).
- Shows the degradation banner when any `meta.surfaces` entry is `"degraded"` or `"unavailable"`.
- Renders loading, empty, degraded, and error states as distinct visual states.

### `DeploymentPlanDetail.tsx`

- Receives `plan_id` as a prop.
- Fetches from `GET /api/v1/operator/deployment-review/{plan_id}` on mount.
- Renders `approval_decision`, `bindings`, `capital_pool`, `runtime_binding`, `latestRun.progress`, `review.riskSummary`, and `review.governanceOutcome`.
- Renders Approve, Reject, and Promote to paper CTAs only when the corresponding `allowedActions` field is `true`.
- Disables all CTAs and shows the degradation banner when any `meta.surfaces` entry is degraded or unavailable.
- On CTA click, calls `POST /api/v1/operator/commands` with the appropriate payload.
- Renders loading, error, and degraded states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- CTA visibility (`canApprove`, `canReject`, `canPromoteToPaper`) must come from `allowedActions` in the BFF response. Do not derive eligibility locally.
- If a required `allowedActions` field is absent from the BFF response, write `.coordination/requests/PKT-001-deployment-review-bff-gap.yaml` using `.coordination/requests/PKT-001-deployment-review-bff-gap.example.yaml` as the template and stop implementation.
- Do not invent fields or supplement the BFF response with client-derived values.
- Filters must be passed as query parameters to the BFF — do not filter client-side.

## Degradation Handling

When `meta.surfaces` contains any entry with status `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.
- Disable all CTAs on the affected surface.
- Do not hide the content — show it read-only with the banner visible.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-001-deployment-review-ui-done.yaml` using `.coordination/requests/PKT-001-deployment-review-ui-done.example.yaml` as the template. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step automatically.

## References

- BFF contract: `docs/bff/PKT-001-deployment-review-console.md`
- Screen spec: `docs/screens/PKT-001-deployment-review-console.md`
- Example payload: `docs/examples/PKT-001-deployment-review-console.json`
- Contract-ready: `.coordination/responses/PKT-001-deployment-review-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-001-deployment-review-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-001-deployment-review-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-001-deployment-review-ui-done.example.yaml`

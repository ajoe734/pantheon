# F-042 Promotion Review — Frontend Change Spec

## Feature

- Feature ID: `F-042`
- Screen ID: `screen-governance-promotion-review`
- Workbench: Governance Workbench
- Packet status: ready

## Summary

Build the **Promotion Review** screen inside `front-ai-trading-system`. This screen lets an operator review whether a deployment plan can move into paper trading without reconstructing governance, deployment, or runtime state in the browser. All screen state and CTA authority must come from Pantheon BFF fields only.

## Files to Create or Modify

```
src/pages/promotion/PromotionReview.tsx                      — existing Promotion Review screen entrypoint and shell
src/pages/promotion/types.ts                                 — Promotion Review response types and surface status union
src/lib/bffClient.ts                                         — Promotion Review read and command helpers
```

Use the existing promotion page modules already present in `front-ai-trading-system`.
Do not create a parallel `src/app/...` or `src/features/...` tree for this handoff.

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

Before continuing UI implementation, apply these frontend-side integration fixes that caused the
original `F-042-bff-gap` handoff:

- Send `Authorization: Bearer <token>` on every stateful Pantheon BFF request in `src/lib/bffClient.ts`.
- Parse the standard `detail.error.*` envelope from Pantheon BFF responses in `src/lib/bffClient.ts`.
  Do not regress to an `errors[]` client contract.
- Use `'unavailable'` as the surface status variant in `src/pages/promotion/types.ts`.
  Do not regress to `'error'` for this state.

### Fetch promotion review payload

```
GET /api/v1/operator/deployment-review/{plan_id}
```

Expected response shape (see `docs/examples/F-042-review-page.json` for the full example):

```typescript
interface PromotionReviewResponse {
  data: {
    deployment_plan: {
      id: string;
      stage: string;
      artifact_id: string;
      approval_decision_id: string;
      approval_decision: ApprovalDecision;
    };
    approval_decision: ApprovalDecision;
    capital_pool: {
      id: string;
      status: string;
    };
    bindings: Array<{
      id: string;
      persona_id: string;
      capital_pool_id: string;
    }>;
    runtime_binding: {
      id: string;
      deployment_stage: string;
      status: string;
    };
    allowedActions: {
      canPromoteToPaper: boolean;
    };
    latestRun: {
      progress: number | null;
    };
    review: {
      riskSummary: string;
      governanceOutcome: string;
      decisionState?: string;
      decidedAt?: string;
      reviewer?: string;
    };
  };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
  };
}

interface ApprovalDecision {
  id: string;
  outcome: string;
  reviewer: string;
  decided_at: string;
  risk_level: string;
  state: string;
}
```

### Submit promotion command

```
POST /api/v1/operator/commands
```

Use the published command contract only. Do not invent a screen-local endpoint or alternate payload shape.

Approve example:

```json
{
  "command": "ApproveDeployment",
  "target": {
    "type": "DeploymentPlan",
    "id": "plan-F-042"
  },
  "action": "approve",
  "params": {
    "deployment_plan_id": "plan-F-042",
    "approval_decision": "approve",
    "verification_notes": "Promotion review approved in UI.",
    "verification_timestamp": "2026-04-12T00:00:00Z"
  },
  "audit_context": {
    "reason": "Promotion review approval.",
    "timestamp": "2026-04-12T00:00:00Z"
  }
}
```

Reject uses the same payload shape with `"action": "reject"` and `"approval_decision": "reject"`.

## Component Structure

### `PromotionReview.tsx`

- Render the header with feature title, deployment artifact identity, target stage, and readiness badge.
- Render the review summary using backend-shaped fields only:
  - `approval_decision.outcome`
  - `approval_decision.state`
  - `approval_decision.decided_at`
  - `review.governanceOutcome`
  - `review.riskSummary`
  - `latestRun.progress`
- Treat `review.decisionState`, `review.decidedAt`, and `review.reviewer` as optional
  echoes. Do not require them when the canonical `approval_decision.*` fields are present.
- Render the allowed actions panel from `allowedActions.canPromoteToPaper` only. Do not derive CTA visibility from plan stage, approval outcome, or runtime status.
- Render supporting evidence from the published response fields and trace references. Do not synthesize extra governance or safety summaries in the client.
- Render loading, empty, degraded, and error states as explicitly distinct states. Do not fall back to mock content.

### Route wiring

- Read the `plan_id` route context or screen parameter required for `GET /api/v1/operator/deployment-review/{plan_id}`.
- Delegate all network activity to the shared BFF client layer.
- Pass only backend-shaped data into the shell component. Do not reshape missing fields into local defaults.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use demo providers, mock stores, or sample data fixtures in production components.
- Render approval decision and governance outcome from backend-shaped fields only.
- CTA visibility and enabled state must come from `allowedActions.canPromoteToPaper` only.
- If any required response field is absent, write `.coordination/requests/F-042-bff-gap.yaml` using `.coordination/requests/F-042-bff-gap.example.yaml` as the template and stop implementation.
- Do not invent endpoint fields beyond the published handoff packet.

## Degradation Handling

When any `meta.surfaces.*.status` is `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner.
- Keep the current payload visible; do not replace degraded sections with invented fallback text.
- Do not infer promotion safety locally. If CTA authority is ambiguous or missing, emit the `bff-gap` handoff instead of guessing.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/F-042-ui-done.yaml` using `.coordination/requests/F-042-ui-done.example.yaml` as the template. If the cycle also produces a review bundle, pair it with `.coordination/requests/F-042-frontend-feedback.yaml` and the `docs/pantheon-feedback/F-042/` artifacts.

## References

- Screen spec: `docs/screens/F-042-promotion-review.md`
- BFF contract: `docs/bff/F-042-promotion-review.md`
- Example payload: `docs/examples/F-042-review-page.json`
- Contract-ready: `.coordination/responses/F-042-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/F-042-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/F-042-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/F-042-ui-done.example.yaml`

# PKT-007 Governance Deployment Diff — Frontend Change Spec

## Feature

- Feature ID: `PKT-007-deployment-diff`
- Screen ID: `screen-governance-deployment-diff`
- Workbench: Governance Workbench
- Packet status: ready

## Summary

Build the **Governance Deployment Diff** screen inside `front-ai-trading-system`. This screen shows a governance operator the field-level diff between a deployment plan and its predecessor so they can evaluate changes, verify risk tier annotations, and proceed to approval or escalation. All diff data and CTA authority must come from Pantheon BFF — the UI must not construct diffs from raw plan fields.

## Files to Create or Modify

```
src/pages/governance/GovernanceDeploymentDiff.tsx  — new deployment diff page
src/pages/governance/types.ts                       — add deployment diff types
src/lib/bffClient.ts                                — add deployment-diff fetch and command helpers
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch deployment diff

```
GET /api/v1/operator/deployment-diff/{plan_id}
Path param: plan_id
```

Expected response shape (see `docs/examples/PKT-007-deployment-diff.json` for a full example):

```typescript
interface DeploymentDiffResponse {
  plan_id: string;
  artifact_id: string;
  stage: string;
  submitted_at: string;
  submitted_by: string;
  previous_plan_id: string | null;
  first_deployment: boolean;
  changes: DiffEntry[];
  change_summary: {
    total_changes: number;
    by_category: Record<string, { count: number; highest_risk_tier: string | null }>;
  };
  allowedActions: {
    canProceedToApproval: boolean;
    canEscalateDiff: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
  };
}
interface DiffEntry {
  field_path: string;
  previous_value: string | null;
  current_value: string;
  change_reason: string;
  change_category: "parameters" | "bindings" | "capital_allocation" | "risk_controls" | "stage_transition";
  risk_tier: "low" | "medium" | "high" | "critical";
}
```

### Escalate diff

```
POST /api/v1/operator/commands
```

```json
{
  "command": "EscalateDiff",
  "target": { "type": "DeploymentPlan", "id": "{plan_id}" },
  "action": "escalate_diff",
  "params": { "plan_id": "{plan_id}", "escalation_reason": "<required>" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

## Component Structure

### `GovernanceDeploymentDiff.tsx`

- Reads `plan_id` from route context or screen parameter.
- Fetches from `GET /api/v1/operator/deployment-diff/{plan_id}` on mount.
- Renders the plan identity header (`plan_id`, `artifact_id`, `stage`, `submitted_at`, `submitted_by`, `previous_plan_id`).
- Renders the change summary rail using `change_summary.by_category`.
- Renders the field diff table from `changes[]` — one row per changed field.
- When `first_deployment` is `true`, shows the `first deployment — no prior plan baseline` message instead of the diff table.
- Renders the approval gating panel from `allowedActions` only.
- Shows the degradation banner when any `meta.surfaces` entry is `"degraded"` or `"unavailable"`.
- When `meta.surfaces.deployment_diff` is `"unavailable"`, replaces the diff table with the degraded-state message and disables the approval CTA.
- Renders loading, empty, degraded, and error states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not construct the diff from raw deployment plan fields. All diff entries must come from `changes[]` in the BFF response.
- CTA visibility (`canProceedToApproval`, `canEscalateDiff`) must come from `allowedActions` in the BFF response only.
- Risk tier labels are rendered as supplied by the BFF. Do not reclassify or override risk tiers.
- If a required `allowedActions` field is absent from the BFF response, write `.coordination/requests/PKT-007-deployment-diff-bff-gap.yaml` and stop implementation.
- Do not invent fields or supplement the BFF response with client-derived values.

## Degradation Handling

When `meta.surfaces.deployment_diff` is `"unavailable"`:

- Replace the diff table with the unavailable-data message.
- Disable the `canProceedToApproval` CTA.

When any other `meta.surfaces` entry is `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.
- Keep the diff table visible in read-only mode.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step.

## References

- BFF contract: `docs/bff/PKT-007-deployment-diff.md`
- Screen spec: `docs/screens/PKT-007-deployment-diff.md`
- Example payload: `docs/examples/PKT-007-deployment-diff.json`
- Contract-ready: `.coordination/responses/PKT-007-deployment-diff-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-007-deployment-diff-lovable-ui-task.yaml`

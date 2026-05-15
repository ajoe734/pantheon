# PKT-008 Governance Rollback Review — Frontend Change Spec

## Feature

- Feature ID: `PKT-008-rollback-review`
- Screen ID: `screen-governance-rollback-review`
- Workbench: Governance Workbench
- Packet status: ready

## Summary

Build the **Governance Rollback Review** screen inside `front-ai-trading-system`. This screen lets a governance operator inspect a pending rollback request — scope, position impact, affected bindings, and trigger evidence — and approve or reject it safely. All screen state and CTA authority must come from Pantheon BFF fields only. The UI must not derive position impact from raw binding or telemetry data.

## Files to Create or Modify

```
src/pages/governance/GovernanceRollbackReview.tsx  — new rollback review page
src/pages/governance/types.ts                       — add rollback review types
src/lib/bffClient.ts                                — add rollback-review fetch and command helpers
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch rollback review

```
GET /api/v1/operator/rollback-review/{rollback_id}
Path param: rollback_id
```

Expected response shape (see `docs/examples/PKT-008-rollback-review.json` for a full example):

```typescript
interface RollbackReviewResponse {
  rollback_id: string;
  target_plan_id: string;
  trigger_reason: string;
  requested_at: string;
  requested_by: string;
  rollback_scope: "full" | "partial";
  affected_persona_count: number;
  affected_binding_count: number;
  target_stage: string;
  position_impact: PositionImpactRow[];
  affected_bindings: AffectedBinding[];
  trigger_evidence: {
    trigger_reason: string;
    evidence_refs: Array<{ ref_id: string; type: string; url: string | null }>;
    linked_incident_id: string | null;
  };
  allowedActions: {
    canApproveRollback: boolean;
    canRejectRollback: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
  };
}
interface PositionImpactRow {
  binding_id: string;
  persona_id: string;
  current_stage: string;
  target_stage: string;
  position_impact_summary: string | null;
  position_data_stale: boolean;
}
interface AffectedBinding {
  binding_id: string;
  persona_id: string;
  capital_pool_id: string;
  current_stage: string;
}
```

### Approve rollback

```
POST /api/v1/operator/commands
```

```json
{
  "command": "ApproveRollback",
  "target": { "type": "Rollback", "id": "{rollback_id}" },
  "action": "approve",
  "params": { "rollback_id": "{rollback_id}", "approval_notes": "" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

### Reject rollback

```json
{
  "command": "RejectRollback",
  "target": { "type": "Rollback", "id": "{rollback_id}" },
  "action": "reject",
  "params": { "rollback_id": "{rollback_id}", "rejection_reason": "<required>" },
  "audit_context": { "reason": "<required operator note>", "timestamp": "<RFC3339>" }
}
```

## Component Structure

### `GovernanceRollbackReview.tsx`

- Reads `rollback_id` from route context or screen parameter.
- Fetches from `GET /api/v1/operator/rollback-review/{rollback_id}` on mount.
- Renders the rollback identity header (`rollback_id`, `target_plan_id`, `trigger_reason`, `requested_at`, `requested_by`, `rollback_scope`).
- Renders the scope summary (`affected_persona_count`, `affected_binding_count`, `target_stage`).
- Renders the position impact table from `position_impact[]`. For each row where `position_data_stale` is `true`, renders the stale-data badge and a `position data stale — impact unknown` message instead of `position_impact_summary`.
- Renders the affected bindings panel from `affected_bindings[]`.
- Renders the trigger evidence drawer on user interaction; content comes from `trigger_evidence`.
- Renders the approval actions panel from `allowedActions` only.
- Disables the Approve CTA when `meta.surfaces.position_data` is `"degraded"` or `"unavailable"`, even if `allowedActions.canApproveRollback` is `true`.
- Shows the degradation banner when any `meta.surfaces` entry is `"degraded"` or `"unavailable"`.
- Renders loading, empty, degraded, and error states as distinct visual states.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not derive position impact from raw binding or telemetry data. All position impact fields come from `position_impact[]` in the BFF response.
- The Approve CTA must be disabled whenever `meta.surfaces.position_data` is `"degraded"` or `"unavailable"` regardless of `allowedActions.canApproveRollback`.
- CTA visibility (`canApproveRollback`, `canRejectRollback`) must come from `allowedActions` in the BFF response only.
- If a required `allowedActions` field is absent from the BFF response, write `.coordination/requests/PKT-008-rollback-review-bff-gap.yaml` and stop implementation.
- Do not invent fields or supplement the BFF response with client-derived values.
- Rollback authority semantics are governed by `ROLLBACK_AND_POSITION_SEMANTICS.md`; this packet renders them, it does not redefine them.

## Degradation Handling

When `meta.surfaces.position_data` is `"degraded"` or `"unavailable"`:

- Show the stale-data warning on all affected position impact rows.
- Disable the Approve CTA.
- Do not hide the position impact table — show it in read-only mode with the stale badge.

When any `meta.surfaces` entry is `"degraded"` or `"unavailable"`:

- Show a non-dismissable degradation banner at the top of the screen.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-008-rollback-review-ui-done.yaml`. Sync the file back to GitHub so the Pantheon supervisor can pick up the next integration step.

## References

- BFF contract: `docs/bff/PKT-008-rollback-review.md`
- Screen spec: `docs/screens/PKT-008-rollback-review.md`
- Example payload: `docs/examples/PKT-008-rollback-review.json`
- Contract-ready: `.coordination/responses/PKT-008-rollback-review-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-008-rollback-review-lovable-ui-task.yaml`
- Rollback semantics policy: `ROLLBACK_AND_POSITION_SEMANTICS.md`

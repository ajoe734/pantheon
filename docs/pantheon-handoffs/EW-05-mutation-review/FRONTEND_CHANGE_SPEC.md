# EW-05 Mutation Review — Frontend Change Spec

## Feature

- Feature ID: `EW-05-mutation-review`
- Screen ID: `screen-evolution-mutation-review`
- Workbench: Evolution Workbench
- Packet status: contract-ready — UI implementation may proceed against the live BFF routes
- Task: `EW-05-OPEN-001`

## Readiness Gate

Pantheon has confirmed **all three** of the following:

1. `GET /api/v1/operator/mutation-review/{decision_id}` is live and returning the published field shape.
2. `POST /api/v1/operator/commands` accepts `ApproveMutation` and `RejectMutation` with the published command vocabulary.
3. `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` are present in the BFF response.

Build the production page on `/evolution/mutation-review/:decision_id` against
these live surfaces. If any required field is absent or diverges from the
synced contract, emit `.coordination/requests/EW-05-mutation-review-bff-gap.yaml`
instead of inventing decision state or dummy CTAs.

## Summary

Build the **Mutation Review** screen inside `front-ai-trading-system`. This screen lets an authorized operator review a pending `EvolutionDecision`, inspect the evidence and proposed changes, and approve or reject the mutation via backend-shaped commands. All data and CTA visibility come from the Pantheon BFF — no client-side authority inference.

## Files to Create or Modify

```
src/pages/evolution/MutationReview.tsx          — new Mutation Review page
src/pages/evolution/MutationReviewTypes.ts       — add mutation-review types
src/lib/bffClient.ts                             — add mutation-review fetch and command calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch mutation review (EW-05 read)

```
GET /api/v1/operator/mutation-review/{decision_id}
Path param: decision_id (required)
```

Expected response shape (see `docs/examples/EW-05-mutation-review.json` for a full example):

```typescript
interface MutationReviewProjection {
  decision_id: string;
  target_type: string;
  target_id: string;
  target_version: string;
  action_type: string;
  decision_state: string;
  risk_level: "low" | "medium" | "high";
  created_at: string;
  approval_decision_id: string | null;

  proposed_changes: {
    summary: string;
    target_stage: string | null;
    downstream_plane: string | null;
    change_details: Array<{
      field: string;
      current_value: string | null;
      proposed_value: string | null;
      note: string | null;
    }>;
  };

  risk_assessment: {
    risk_summary: string;
    severity: string | null;
    threshold_triggers: Array<{
      trigger_type: string;
      metric: string;
      observed_value: string;
      threshold_value: string;
      threshold_source: string;
    }>;
  };

  required_approvals: Array<{
    role: string;
    approved_by: string | null;
    approved_at: string | null;
    status: "pending" | "approved" | "rejected";
  }>;

  review_chain: Array<{
    action: string;
    actor_role: string;
    actor_id: string;
    acted_at: string;
    note: string | null;
  }>;

  linked_incident_id: string | null;
  linked_postmortem_id: string | null;

  evidence_refs: Array<{
    ref_type: string;
    ref_id: string;
    summary: string;
  }>;

  rollback_followthrough: {
    rollback_request_ref: string | null;
    rollback_action_type: string | null;
    followthrough_note: string | null;
  } | null;

  allowedActions: {
    canApproveMutation: boolean;
    canRejectMutation: boolean;
  };

  meta: {
    snapshot_at: string;
    surfaces: {
      mutation_review: "fresh" | "stale" | "unavailable";
    };
  };
}
```

### Submit mutation-review command (EW-05 write)

```
POST /api/v1/operator/commands
```

#### ApproveMutation payload

```typescript
{
  command_type: "ApproveMutation",
  decision_id: string,
  note?: string
}
```

#### RejectMutation payload

```typescript
{
  command_type: "RejectMutation",
  decision_id: string,
  note?: string
}
```

Expected success response:

```typescript
{
  command_accepted: true,
  decision_id: string,
  new_state: "approved" | "rejected",
  committed_at: string
}
```

## Component Structure

### `MutationReview.tsx`

- Route: `/evolution/mutation-review/:decision_id`.
- If `decision_id` is absent from the route, render an explicit prompt. Do not render an empty decision panel.
- Fetches `GET /api/v1/operator/mutation-review/{decision_id}` on mount and after a successful command.

#### Decision Context Header

- Render `decision_id`, `target_type`, `target_id`, `action_type`, `risk_level` (color-coded badge: red = high, amber = medium, green = low), `decision_state`, and `created_at`.
- Render `approval_decision_id` as a link if present.
- Do not compute state labels or risk colors from raw enum values by fallback logic. Use a display map keyed on BFF field values.

#### Proposed Changes Panel

- Render `proposed_changes.summary` as the primary narrative.
- Render `proposed_changes.target_stage` and `proposed_changes.downstream_plane` as labeled fields when non-null.
- Render `proposed_changes.change_details[]` as a structured table: Field / Current Value / Proposed Value / Note.
- Do not compute or narrate change semantics client-side.

#### Incident and Postmortem Evidence Rail

- Render `linked_incident_id` as a labeled link when non-null.
- Render `linked_postmortem_id` as a labeled link when non-null.
- Render `evidence_refs[]` as a typed list: each row shows `ref_type`, `ref_id`, and `summary`.
- If both linked IDs are null and `evidence_refs` is empty, show "No linked incident, postmortem, or evidence refs." Do not hide the panel.

#### Rollback Follow-Through Panel

- Render `rollback_followthrough.rollback_request_ref`, `rollback_action_type`, and `followthrough_note` when `rollback_followthrough` is non-null.
- If `rollback_followthrough` is null, show "No rollback follow-through associated with this decision."
- No write actions on this panel.

#### Risk Assessment Panel

- Render `risk_assessment.risk_summary` as the primary narrative.
- Render `risk_assessment.threshold_triggers[]` as a structured list: Trigger Type / Metric / Observed / Threshold / Source.
- Render `risk_assessment.severity` when non-null.
- Do not compute risk level or threshold breach state client-side.

#### Required Approvals

- Render `required_approvals[]` as a checklist.
- Each row: Role / Approved By / Approved At / Status badge (`pending` / `approved` / `rejected`).

#### Review Chain

- Render `review_chain[]` as a chronological audit trail.
- Each row: Action / Actor Role / Actor ID / Acted At / Note.

#### Approve / Reject CTA

**Only render the Approve CTA when `allowedActions.canApproveMutation === true` AND `meta.surfaces.mutation_review !== "unavailable"`.**

**Only render the Reject CTA when `allowedActions.canRejectMutation === true` AND `meta.surfaces.mutation_review !== "unavailable"`.**

CTA rules:
- If `canApproveMutation` is not present in the response, suppress the Approve CTA.
- If `canRejectMutation` is not present in the response, suppress the Reject CTA.
- When degradation is `"unavailable"`, suppress both CTAs regardless of `allowedActions`.
- CTAs may optionally show a note text input before confirming — use a confirmation modal.
- After submitting a command, re-fetch the read route to confirm state. Do not optimistically update `decision_state`.
- Display submission errors from the command response and allow retry.

## Degradation Handling

| `meta.surfaces.mutation_review` | Required behavior |
|---|---|
| `"fresh"` | Normal display |
| `"stale"` | Non-dismissable staleness banner at top; data visible with stale caveat; CTA visibility still follows `allowedActions` |
| `"unavailable"` | Replace panel content with degradation notice; suppress both CTAs |

## State Requirements

Each data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `stale`: stale banner with available data
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

Do not map `stale` to `empty`.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` are the sole sources of CTA visibility truth. No inference from `risk_level`, `decision_state`, or actor role.
- The screen reviews mutation authority only. It must not create a parallel `ApprovalDecision`, submit runtime rollback commands, deploy or redeploy artifacts, or mutate `RuntimeBinding`, `DeploymentPlan`, or incident objects.
- Incident and rollback evidence are read-only references.
- If any required field is absent from the BFF response, write `.coordination/requests/EW-05-mutation-review-bff-gap.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/EW-05-mutation-review-ui-done.yaml`.

## References

- Screen spec: `docs/screens/EW-05-mutation-review.md`
- BFF contract: `docs/bff/EW-05-mutation-review.md`
- Example payload: `docs/examples/EW-05-mutation-review.json`
- Contract-ready coordination: `.coordination/responses/EW-05-mutation-review-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`
- Evolution policy: `EVOLUTION_REVIEW_AND_THRESHOLDS.md`

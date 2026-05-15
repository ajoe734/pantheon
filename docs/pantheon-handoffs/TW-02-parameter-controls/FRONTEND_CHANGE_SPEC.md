# TW-02 Parameter Controls — Frontend Change Spec

## Feature

- Feature ID: `TW-02-parameter-controls`
- Screen ID: `screen-parameter-controls`
- Workbench: Trainer Workbench
- Packet status: `route-live` — UI implementation may proceed against the live BFF routes
- Task: `TW-02-CONTROLS-001`

## Readiness Gate

Pantheon has confirmed both trainer-controls routes are live and returning the
published field shape:

1. `GET /api/v1/trainer/sessions/{session_id}/controls` returns the mutable
   control-state object, `allowedActions.canPatchControls`,
   `meta.staleness`, and `meta.surfaces.trainer_controls.state`.
2. `POST /api/v1/trainer/sessions/{session_id}/patch` accepts
   `patches[] = [{parameter_key, proposed_value}]` and returns either:
   - `status: "accepted"` with `warnings[]`, `diff.updated_controls[]`, and
     `current_controls[]`, or
   - `status: "rejected"` with `error_code`, `field_errors[]`,
     `rejected_changes[]`, and `current_controls[]`.
3. The patch route returns `409` when session `status != active` or when
   `allowedActions.canPatchControls` is false.

Build the production page against these live surfaces. If any required field is
absent or diverges from the synced contract, emit
`.coordination/requests/TW-02-parameter-controls-bff-gap.yaml` instead of
inventing ranges, clipping values, or synthesizing diffs client-side.

## Summary

Build the **Parameter Controls** screen inside `front-ai-trading-system`. This
slice lets an operator inspect backend-owned trainer controls, submit governed
partial patches, and render accepted or rejected feedback without mutating
against stale or degraded data. All control metadata, write authority,
validation errors, accepted diffs, and freshness cues come from the Pantheon
BFF.

## Files to Create or Modify

```text
src/pages/trainer/ParameterControls.tsx       — controls page for one trainer session
src/pages/trainer/types.ts                    — add trainer-controls and patch-response types
src/lib/bffClient.ts                          — add TW-02 controls read and patch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch`
or `axios` calls in component files.

### Get trainer controls

```http
GET /api/v1/trainer/sessions/{session_id}/controls
```

Required response fields:

- `object_ref`
- `session_id`
- `status`
- `controls[]`
- `allowedActions.canPatchControls`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.trainer_controls.state`

Each `controls[]` row includes at least:

- `control_id`
- `parameter_key`
- `display_label`
- `control_type`
- `current_value`
- `allowed_range`
- `unit`
- `last_modified_at`

### Patch trainer controls

```http
POST /api/v1/trainer/sessions/{session_id}/patch
```

Request body:

```typescript
interface TrainerControlPatchRequest {
  patches: Array<{
    parameter_key: string;
    proposed_value: unknown;
  }>;
}
```

Accepted response shape:

```typescript
interface TrainerControlPatchAccepted {
  session_id: string;
  status: "accepted";
  message: string;
  warnings: Array<{ code?: string; message?: string }>;
  diff: {
    updated_controls: Array<{
      field: string;
      before: unknown;
      after: unknown;
      validation_status: "accepted" | "warning";
    }>;
  };
  current_controls: TrainerControl[];
  allowedActions: { canPatchControls: boolean };
  meta: TrainerControlMeta;
}
```

Rejected validation shape:

```typescript
interface TrainerControlPatchRejected {
  session_id: string;
  status: "rejected";
  error_code: string;
  message: string;
  field_errors: Array<{
    field: string;
    reason: string;
    current_value: unknown;
    requested_value: unknown;
    allowed_range: Record<string, unknown> | null;
  }>;
  rejected_changes: unknown[];
  current_controls: TrainerControl[];
  allowedActions: { canPatchControls: boolean };
  meta: TrainerControlMeta;
}
```

Precondition failures:

- `409 PRECONDITION_NOT_MET` when `allowedActions.canPatchControls` is false
- `409 INVALID_STATE` when session `status != active`

Do not convert those `409` responses into optimistic UI success or silent no-op
states. Surface the backend error and re-fetch `GET /controls`.

## Component Structure

### `ParameterControls.tsx`

- Route: `/trainer/sessions/:session_id/controls`
- Reads `session_id` from the route and fetches
  `GET /api/v1/trainer/sessions/{session_id}/controls` on mount.
- Renders the full control-state object, current values, range metadata, and
  freshness cues from the BFF response only.
- Uses `control_type` to choose the correct input primitive (`number`,
  `integer`, `enum`, `boolean`).
- Shows the patch CTA only when `allowedActions.canPatchControls === true` and
  `meta.surfaces.trainer_controls.state === "ok"`.
- On submit, sends only the published `patches[]` request body.
- On `status: "accepted"`, renders `warnings[]` plus
  `diff.updated_controls[]`, then refreshes the panel from `current_controls[]`.
- On `status: "rejected"`, renders row-level feedback from `field_errors[]`
  without mutating the visible baseline control values.
- On `409`, surfaces the backend error, re-fetches the controls route, and does
  not invent fallback authority state.

## Constraints

- Use the dedicated controls route only. Do not reconstruct control state from
  `TW-01` session detail, Persona drilldowns, or local page state.
- Do not clip `proposed_value` into `allowed_range` before submission.
- Do not derive rejection copy, warning severity, or diff rows client-side.
- Do not infer mutation authority from `status` alone; use
  `allowedActions.canPatchControls`.
- Do not derive accepted diffs from a prior controls fetch; use
  `diff.updated_controls[]` only.
- If any required field is missing, emit the TW-02 `bff-gap` handoff instead of
  mocking or inventing a placeholder contract.

## Degradation Handling

| Signal | Required behavior |
|---|---|
| `meta.surfaces.trainer_controls.state = "ok"` | normal control rendering |
| `meta.staleness.status = "stale"` | render a non-dismissable staleness banner over the current surface; patch CTA still depends on explicit authority plus surface state |
| `meta.surfaces.trainer_controls.state = "degraded"` | show the canonical PKT-005 degradation banner, keep last-known controls visible, and suppress the patch CTA |
| `meta.surfaces.trainer_controls.state = "unavailable"` | suppress the control panel and editor and show an unavailable notice |

Do not treat an empty `controls[]` array as authoritative when the surface is
`degraded` or `unavailable`.

## Completion Handoff

When the UI implementation is ready:

1. Write `.coordination/requests/TW-02-parameter-controls-ui-done.yaml`.
2. Publish the standard frontend feedback bundle under
   `docs/pantheon-feedback/TW-02-parameter-controls/`.
3. Keep the `ui-done` handoff and feedback bundle aligned to one truthful front
   commit.

## References

- BFF contract: `docs/bff/TW-02-parameter-controls.md`
- Screen spec: `docs/screens/TW-02-parameter-controls.md`
- Example payload: `docs/examples/TW-02-parameter-controls.json`
- Packet family: `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- Frontend SA: `docs/lovable/PANTHEON_FRONTEND_SA.md`

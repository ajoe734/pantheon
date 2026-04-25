# TW-02 Parameter Controls

## Classification

- Workbench: Trainer Workbench
- Screen ID: `screen-parameter-controls`
- Feature ID: `TW-02-parameter-controls`
- Packet status: `route-live` — the controls read and patch routes are live; frontend should implement against the current BFF contract
- Task: `TW-02-CONTROLS-001`

## Contract Note

The Trainer controls surface is now live. The frontend must read control state
from `GET /api/v1/trainer/sessions/{session_id}/controls` and submit patches
through `POST /api/v1/trainer/sessions/{session_id}/patch` only. All control
metadata, patch authority, validation failures, accepted diffs, surface state,
and freshness come from the Pantheon BFF.

The UI must not infer editable ranges, clip invalid values into range, or
reconstruct accepted diffs from a previous controls fetch.

## User Goal

Let an operator inspect the current mutable control state for one trainer
session, submit a governed partial patch against backend-defined ranges, and
see authoritative rejection, warning, or accepted-diff feedback without
mutating against degraded or unavailable data.

## Routes

Primary route:

- `/trainer/sessions/:session_id/controls`

## Readiness Gate

Pantheon has confirmed:

1. `GET /api/v1/trainer/sessions/{session_id}/controls` is live with
   `controls[]`, `allowedActions.canPatchControls`, `meta.staleness`, and
   `meta.surfaces.trainer_controls.state`.
2. `POST /api/v1/trainer/sessions/{session_id}/patch` is live with accepted
   responses shaped as `status: "accepted"`, `warnings[]`,
   `diff.updated_controls[]`, `current_controls[]`,
   `allowedActions.canPatchControls`, and `meta`.
3. Invalid control updates return `status: "rejected"`, `error_code`,
   `field_errors[]`, `rejected_changes[]`, `current_controls[]`,
   `allowedActions.canPatchControls`, and `meta`.
4. The patch route returns a `409` precondition error when session
   `status != active` or when `allowedActions.canPatchControls` is false.

Build the production page against these live routes. No pending-BFF
placeholder, no optimistic patch application, and no fake validation copy.

## Page Sections

### 1. Session Control Header

- Lives on `/trainer/sessions/:session_id/controls`.
- Displays:
  - `session_id`
  - `status`
  - `meta.snapshot_at`
  - `meta.staleness.status`
- `status` is read-side truth inherited from the trainer-session lifecycle.
  `TW-02` does not publish local lifecycle mutation buttons.

### 2. Control State Panel

- Renders `controls[]` from `GET /api/v1/trainer/sessions/{session_id}/controls`.
- Each row shows:
  - `display_label`
  - `parameter_key`
  - `current_value`
  - `unit`
  - `allowed_range`
  - `last_modified_at`
- The UI may group rows visually, but it must not derive labels, ranges, or
  control typing outside the published object.

### 3. Patch Editor

- Submission target: `POST /api/v1/trainer/sessions/{session_id}/patch`
- Request body:
  - `patches[]`
    - `parameter_key`
    - `proposed_value`
- Bind input type from `control_type`:
  - `number`
  - `integer`
  - `enum`
  - `boolean`
- Show the patch CTA only when `allowedActions.canPatchControls === true` and
  `meta.surfaces.trainer_controls.state === "ok"`.

### 4. Validation Feedback Rail

- Render patch response feedback from the backend only.
- Accepted response:
  - show `status = "accepted"`
  - render `warnings[]` when non-empty
  - refresh the control panel from `current_controls[]`
- Rejected response:
  - show `status = "rejected"`
  - render row-level copy from `field_errors[]`
  - keep baseline control values unchanged
- The frontend must not treat missing `warnings[]` or an HTTP `200` as success
  unless the response `status` is `accepted`.

### 5. Inline Diff Panel

- Render from `diff.updated_controls[]` after an accepted patch.
- Each changed row shows:
  - `field`
  - `before`
  - `after`
  - `validation_status`
- When helpful, map `field` back to the matching control's `display_label` from
  `current_controls[]` or the last GET response. Do not infer prior values from
  a cached controls fetch.

## State Handling

| State | Required behavior |
|---|---|
| `allowedActions.canPatchControls = true` and `meta.surfaces.trainer_controls.state = "ok"` | show control panel and patch editor |
| `allowedActions.canPatchControls = false` | show read-only controls; hide patch CTA |
| response `status = "rejected"` | show row-level rejection copy from `field_errors[]`; keep baseline values unchanged |
| response `status = "accepted"` with warnings | show warnings and `diff.updated_controls[]` together |
| response `status = "accepted"` without warnings | show success state with inline diff only |
| HTTP `409` with `PRECONDITION_NOT_MET` or `INVALID_STATE` | surface the backend error, re-fetch GET controls, and do not invent fallback authority |

## Degradation Handling

| Signal | Required behavior |
|---|---|
| `meta.surfaces.trainer_controls.state = "ok"` | normal control rendering |
| `meta.staleness.status = "stale"` | non-dismissable staleness banner layered on the current surface; patch CTA still depends on `allowedActions.canPatchControls` and surface state |
| `meta.surfaces.trainer_controls.state = "degraded"` | show degradation banner, keep last-known controls visible, hide patch CTA |
| `meta.surfaces.trainer_controls.state = "unavailable"` | replace control panel and editor with unavailable notice |

Freshness is represented through `meta.staleness`. Do not invent a separate
`"stale"` surface state.

## Constraints

- Use the dedicated controls route only. Do not reconstruct control state from
  `TW-01` session detail or Persona surfaces.
- Do not clip `proposed_value` into `allowed_range` before submission.
- Do not derive rejection copy, warning severity, or diff rows client-side.
- Do not render mutation controls when `allowedActions.canPatchControls` is
  absent or false.
- Do not treat `status = active` as sufficient mutation authority without the
  explicit `allowedActions` field.
- If any required field is missing, emit a `bff-gap` handoff instead of
  inventing fallback UI.

## Acceptance

- The page renders backend-owned `controls[]` and shows each control's
  published range, type, and current value.
- The patch editor submits only the published `patches[]` body shape.
- Invalid patches render row-level feedback from `field_errors[]` and do not
  change displayed control state.
- Accepted patches render backend-owned `diff.updated_controls[]` and refresh
  the panel from `current_controls[]`.
- Patch CTA visibility follows `allowedActions.canPatchControls` plus the
  published surface state.
- Degradation behavior follows `meta.surfaces.trainer_controls.state` and
  `meta.staleness` exactly.

## References

- BFF contract: `docs/bff/TW-02-parameter-controls.md`
- Example payload: `docs/examples/TW-02-parameter-controls.json`
- Frontend handoff: `docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- Frontend SA: `docs/lovable/PANTHEON_FRONTEND_SA.md`

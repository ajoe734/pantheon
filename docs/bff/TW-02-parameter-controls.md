# TW-02 Parameter Controls BFF Contract

## Status

**Route-live.** The `2026-04-22` follow-up architecture response closed the
remaining patch, validation, and diff-shape questions, and the current BFF now
implements the `GET /controls` and `POST /patch` route family on that ratified
partial-patch contract. Remaining work is frontend activation and delivery
closeout against the live surface.

Task: `TW-02-CONTROLS-001`

## Purpose

Provide the parameter-control surface for the Trainer Workbench so operators
can inspect mutable trainer-session controls, submit governed partial patches,
and render backend-authored validation or diff feedback without clipping
values, inventing ranges, or deriving patch deltas in the browser.

## Dependencies

- `TW-01-FOUNDATION-001` for canonical `session_id`, trainer lifecycle
  semantics, and session `status`

## Routes

### Get trainer session controls

- `GET /api/v1/trainer/sessions/{session_id}/controls`

Required response fields:

- `object_ref`
  - `type = "TrainerControlState"`
  - `id = session_id`
- `session_id`
- `status`
- `controls[]`
- `allowedActions.canPatchControls`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.trainer_controls.state` — `ok | degraded | unavailable`

### Patch trainer session controls

- `POST /api/v1/trainer/sessions/{session_id}/patch`

Required request body:

- `patches[]`
  - `parameter_key`
  - `proposed_value`

Patch semantics:

- patching is field-level partial patch over an allowlisted control surface
- omitted fields remain unchanged
- replace-style patch is not allowed

## Accepted patch response

When a patch is accepted, the response must contain:

- `session_id`
- `status = "accepted"`
- `message`
- `warnings[]`
- `diff.updated_controls[]`
- `current_controls[]`
- `allowedActions.canPatchControls`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.trainer_controls.state`

`diff.updated_controls[]` is the canonical v1 diff source.

Each row in `diff.updated_controls[]` must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `field` | string | no | canonical control field name |
| `before` | any | yes | previous value |
| `after` | any | yes | accepted value |
| `validation_status` | string | no | `accepted \| warning` |

Migration note:

- legacy top-level `updated_controls[]` may still appear temporarily during
  migration
- canonical v1 diff semantics now live under `diff.updated_controls[]`

## Rejected patch response

When a patch is invalid or rejected, the response must contain:

- `session_id`
- `status = "rejected"`
- `error_code`
- `message`
- `field_errors[]`
- `rejected_changes[]`
- `current_controls[]`
- `allowedActions.canPatchControls`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.trainer_controls.state`

Canonical rejected shape:

```json
{
  "status": "rejected",
  "error_code": "CONTROL_PATCH_VALIDATION_FAILED",
  "message": "Patch contains invalid control updates.",
  "field_errors": [
    {
      "field": "max_leverage",
      "reason": "exceeds_allowed_range",
      "current_value": 1.5,
      "requested_value": 5.0,
      "allowed_range": {
        "min": 0.0,
        "max": 2.0
      }
    }
  ],
  "rejected_changes": [],
  "current_controls": [],
  "allowedActions": {
    "canPatchControls": false
  }
}
```

## ControlParameter Object

Each `controls[]` row and each row in `current_controls[]` must conform to this
object.

Required fields:

- `control_id`
- `parameter_key`
- `display_label`
- `control_type` — `number | integer | enum | boolean`
- `current_value`
- `allowed_range`
- `unit` — nullable
- `last_modified_at`

Required invariants:

- `parameter_key` is the durable patch identity. Frontend must not patch by
  array index.
- `display_label` is BFF-resolved and must not be inferred from
  `parameter_key`.
- `current_value` must conform to the declared `control_type`.

## Validation and write authority rules

- `allowedActions.canPatchControls` is the sole mutation-authority signal.
- Frontend must not infer patch authority from `status` alone.
- The BFF must reject the patch route when `status != "active"`.
- The BFF must reject the patch route when `allowedActions.canPatchControls` is
  absent or `false`.
- The BFF must never silently clip a value into `allowed_range`; invalid values
  are rejected explicitly.

## Degradation rules

| `meta.surfaces.trainer_controls.state` | Behavior |
|---|---|
| `ok` | render controls normally |
| `degraded` | show degradation banner, keep last-known controls visible, suppress mutation CTA |
| `unavailable` | suppress control panel and patch editor entirely |

Freshness must be represented through `meta.staleness`, not a primary surface
state of `stale`.

## Non-goals

- The frontend must not clip invalid values into `allowed_range`.
- The frontend must not synthesize diffs from a cached controls response.
- The frontend must not reuse `GET /api/v1/trainer/sessions/{session_id}` as a
  substitute for the dedicated controls route.
- This slice still does not define preview, compare, commit, or discard
  behavior. Those remain `TW-03` and `TW-04` scope.

## Example Payload

- `docs/examples/TW-02-parameter-controls.json`

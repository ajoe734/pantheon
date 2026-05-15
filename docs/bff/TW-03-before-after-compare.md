# TW-03 Before/After Compare BFF Contract

## Status

**Contract published** — the trainer preview read route, manual refresh route, warning hierarchy, `preview_unavailable` degraded semantics, and polling contract are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the routes are live and returning this field shape.

Task: `TW-03-COMPARE-001`

## Purpose

Provide the third real production slice for the Trainer Workbench so operators can compare baseline and candidate trainer-session outcomes, inspect backend-authored metric deltas and warning hierarchy, and understand degraded preview behavior without deriving performance projections, warning severity, or polling heuristics in the browser.

## Dependencies

- `TW-01-FOUNDATION-001` for canonical `session_id`, trainer lifecycle semantics, and session `status`
- `TW-02-CONTROLS-001` for backend-authored `previous_value` / `new_value` control diff semantics

## Routes

### Get trainer preview

- `GET /api/v1/trainer/sessions/{session_id}/preview`

Supported query params:

- `eval_id` — optional; when present, returns the named preview evaluation instead of the latest preview snapshot for the session

Required response fields:

- `session_id`
- `status` — `"complete"` | `"pending"` | `"failed"` | `"preview_unavailable"`
- `eval_id` — nullable only when `status = "preview_unavailable"`
- `baseline_snapshot_at`
- `candidate_snapshot_at`
- `control_diff[]`
- `metric_delta[]`
- `warnings[]`
- `warning_count_by_level`
- `preview_quality`
- `allowedActions.canRefreshPreview`
- `polling`
- `meta.snapshot_at`
- `meta.surfaces.trainer_preview` — `"ok"` | `"stale"` | `"degraded"` | `"unavailable"`

### Refresh trainer preview

- `POST /api/v1/trainer/sessions/{session_id}/preview`

Required request body:

- `refresh_mode` — must be `"manual"`

Required response fields:

- same field shape as `GET /api/v1/trainer/sessions/{session_id}/preview`

Required invariants:

- The BFF must reject preview refresh when `allowedActions.canRefreshPreview` is absent or `false`.
- The BFF must reject preview refresh when session `status` is neither `"active"` nor `"paused"`.
- When an evaluation for the same candidate snapshot is already `pending`, `POST /preview` must return the existing `eval_id` with `status = "pending"` instead of creating duplicate preview jobs.
- The compare surface must render from this dedicated preview route family only. The frontend must not join TW-01 session detail, TW-02 patch responses, or local simulations to recreate compare results.

## Preview Response Object

The preview route returns one backend-composed compare object for the entire Before/After Compare page.

Required fields:

- `session_id`
- `status`
- `eval_id`
- `baseline_snapshot_at`
- `candidate_snapshot_at`
- `control_diff[]`
  - `control_id`
  - `parameter_key`
  - `display_label`
  - `previous_value`
  - `new_value`
  - `unit` — nullable string
  - `last_modified_at`
- `metric_delta[]`
  - `metric_key`
  - `display_label`
  - `baseline_value`
  - `candidate_value`
  - `delta`
  - `delta_pct` — nullable number
  - `unit` — nullable string
  - `direction` — `"improved"` | `"regressed"` | `"unchanged"`
- `warnings[]`
  - `warning_id`
  - `warning_code`
  - `level`
  - `parameter_key` — nullable string
  - `metric_key` — nullable string
  - `message`
  - `impact_summary`
- `warning_count_by_level`
  - `critical`
  - `high`
  - `medium`
  - `informational`
- `preview_quality` — `"high_confidence"` | `"directional_only"` | `"insufficient_data"` | `"not_available"`
- `allowedActions.canRefreshPreview`
- `polling`
  - `enabled`
  - `poll_interval_ms`
  - `max_wait_ms`
  - `deadline_at` — nullable timestamp
- `degraded_copy` — nullable object with:
  - `title`
  - `body`
- `meta.snapshot_at`
- `meta.surfaces.trainer_preview`

Required invariants:

- `control_diff[]` is the backend-owned source for the compare page's control diff panel. The frontend must not reconstruct parameter deltas from a cached TW-02 response.
- `metric_delta[]` is the backend-owned source for all before/after metric panels. The frontend must not derive metric deltas from control values, local preview math, or historical charts.
- `warning_count_by_level` must always include all four hierarchy keys, even when the count is `0`.
- `preview_quality = "not_available"` is only valid when `status = "preview_unavailable"`.
- `degraded_copy` is required whenever `status = "preview_unavailable"` or `meta.surfaces.trainer_preview != "ok"`.

## Warning Hierarchy

The BFF owns the warning taxonomy and display priority.

Allowed levels:

- `critical` — candidate state should not be promoted or trusted for operator decision-making
- `high` — candidate state is materially risky and requires operator review before relying on the preview
- `medium` — preview is usable but operator attention is required for one or more affected controls or metrics
- `informational` — contextual note only; does not by itself imply the candidate is unsafe

Required invariants:

- `warnings[]` must be ordered by backend priority: `critical`, `high`, `medium`, `informational`, then stable by `warning_id`.
- The frontend must not derive warning level from `warning_code`, `impact_summary`, or metric direction.
- `warning_count_by_level` must reconcile exactly with `warnings[]`.

## Preview Status Branches

### Complete preview

When the rapid-eval finishes successfully:

- `status` must be `"complete"`
- `eval_id` must be present
- `metric_delta[]` may be non-empty
- `preview_quality` must not be `"not_available"`
- `polling.enabled` must be `false`

### Pending preview

When the rapid-eval is still running:

- `status` must be `"pending"`
- `eval_id` must be present
- `control_diff[]` must still describe the candidate delta being evaluated
- `metric_delta[]` and `warnings[]` may be empty
- `polling.enabled` must be `true`
- `allowedActions.canRefreshPreview` must be `false`

### Failed preview

When the rapid-eval executed but did not produce a usable compare result:

- `status` must be `"failed"`
- `eval_id` must be present
- `control_diff[]` must still describe the candidate delta that failed evaluation
- `metric_delta[]` must be empty
- `allowedActions.canRefreshPreview` may be `true` if the surface itself remains available
- `degraded_copy` must explain that the rapid-eval failed without exposing internal stack traces or provider error payloads

### `preview_unavailable` degraded contract

When preview infrastructure or supporting evidence is unavailable:

- `status` must be `"preview_unavailable"`
- The route must still return HTTP success with a structured body; this condition must not degrade to a generic `5xx` contract for the UI
- `eval_id` must be `null`
- `metric_delta[]` and `warnings[]` must be empty
- `warning_count_by_level` must be all zeroes
- `preview_quality` must be `"not_available"`
- `allowedActions.canRefreshPreview` must be `false`
- `polling.enabled` must be `false`
- `degraded_copy.title` and `degraded_copy.body` must name the trainer preview surface and explain that rapid-eval results are temporarily unavailable without surfacing internal error codes
- `meta.surfaces.trainer_preview` must be `"degraded"` when the compare page can still render control diff context or `"unavailable"` when no compare content can be shown

## Polling Contract

Async polling is only valid while `status = "pending"`.

Required semantics:

- The frontend must poll `GET /api/v1/trainer/sessions/{session_id}/preview?eval_id={eval_id}`.
- `polling.poll_interval_ms` must be `3000`.
- `polling.max_wait_ms` must be `45000`.
- `polling.deadline_at` must be the backend-authored terminal timestamp for the pending evaluation.
- The frontend must stop polling when:
  - `status != "pending"`
  - `meta.surfaces.trainer_preview` becomes `"degraded"` or `"unavailable"`
  - the current time passes `deadline_at`
- The BFF must not return `status = "pending"` after `deadline_at`. The next response after the deadline must resolve to `"complete"`, `"failed"`, or `"preview_unavailable"`.
- The frontend must not poll faster than `poll_interval_ms` and must not implement its own backoff policy for this slice.

## Write Authority

- `allowedActions.canRefreshPreview` is the sole CTA authority signal for the compare surface refresh action.
- The signal must be `false` whenever:
  - `status = "pending"`
  - `status = "preview_unavailable"`
  - session `status` is neither `"active"` nor `"paused"`
  - `meta.surfaces.trainer_preview` is `"degraded"` or `"unavailable"`
  - the backend cannot guarantee the candidate snapshot is current

The frontend must not infer refresh authority from session `status` alone.

## Degradation Rules

- When `meta.surfaces.trainer_preview = "stale"`, the UI may show the last-known compare result with a non-dismissable staleness banner and backend-authored `degraded_copy`, but the refresh CTA depends on `allowedActions.canRefreshPreview`.
- When `meta.surfaces.trainer_preview = "degraded"`, show the shared degradation substrate from `PKT-005`, preserve only backend-supplied compare content, and suppress refresh.
- When `meta.surfaces.trainer_preview = "unavailable"`, suppress metric panels and refresh entirely; the compare page may show only the canonical unavailable messaging supplied through `degraded_copy`.
- The frontend must not treat an empty `metric_delta[]` array as authoritative when `status = "pending"`, `status = "failed"`, or the surface is `"degraded"` / `"unavailable"`.

## Non-Goals

- The frontend must not derive performance previews from TW-02 `updated_controls[]`, raw backtest results, or local sandbox simulation.
- The frontend must not derive warning severity from metric direction or parameter range proximity.
- The frontend must not poll the refresh route; only the dedicated GET preview route may be polled.
- The frontend must not treat `preview_unavailable` as a loading state.
- This slice does not publish commit, discard, replay event history, or artifact-evidence navigation. Those remain `TW-04` scope.

## Relationship to Downstream Trainer Modules

- `TW-04 Teaching Replay` depends on `eval_id`, `baseline_snapshot_at`, `candidate_snapshot_at`, and backend-authored compare results so replay events can reference stable preview evidence without recomputing compare state.

## Example Payload

- `docs/examples/TW-03-before-after-compare.json`

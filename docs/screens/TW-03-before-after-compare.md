# TW-03 Before/After Compare

## Classification

- Workbench: Trainer Workbench
- Screen ID: `screen-before-after-compare`
- Feature ID: `TW-03-before-after-compare`
- Packet status: **route-live** — preview read/refresh routes are live, and the frontend handoff bundle is published for production UI activation
- Task: `TW-03-COMPARE-001`

## Contract Note

The Trainer Workbench now has a route-live compare slice for previewing candidate changes against the current baseline. Pantheon has confirmed that the preview read route and manual refresh route are live and returning the published field shape, so production UI may proceed against this route family.

The UI must not derive metric deltas, warning levels, control diff rows, degraded preview copy, or polling behavior locally. All compare truth comes from the Pantheon BFF preview response. If the live payload diverges from the synced contract, emit the canonical TW-03 `bff-gap` handoff instead of inventing a fallback.

## User Goal

Let an operator inspect the current candidate-vs-baseline compare result for one trainer session, review backend-authored metric deltas and warning hierarchy, understand degraded preview behavior truthfully, and request a manual refresh without triggering duplicate or unsafe polling behavior.

## Routes

Primary route:

- `/trainer/sessions/:session_id/compare`

## Readiness Gate

Pantheon has already confirmed the following production gate for TW-03:

1. `GET /api/v1/trainer/sessions/{session_id}/preview` is live with `control_diff[]`, `metric_delta[]`, `warnings[]`, `warning_count_by_level`, `preview_quality`, `polling`, and `meta.surfaces.trainer_preview`.
2. `POST /api/v1/trainer/sessions/{session_id}/preview` is live with the published `refresh_mode = "manual"` body and the same response contract as the read route.
3. `warnings[]` always use backend-authored `level` values from the published hierarchy (`critical`, `high`, `medium`, `informational`).
4. Pending preview responses expose the published polling contract and never remain `pending` after `polling.deadline_at`.
5. `preview_unavailable` returns the published degraded branch with backend-authored copy instead of a generic empty state or loading spinner.

The production page may open against this route family now. If the live payload diverges from the synced contract, emit a `bff-gap` handoff instead of reintroducing a pending-BFF placeholder, fake warning ladder, or local preview math.

## Page Sections

### 1. Compare Header

- Lives on `/trainer/sessions/:session_id/compare`.
- Displays:
  - `session_id`
  - `status`
  - `eval_id`
  - `baseline_snapshot_at`
  - `candidate_snapshot_at`
  - `meta.snapshot_at`
- `status` is the preview lifecycle for the compare surface, not the trainer session lifecycle.

### 2. Rapid-Eval Summary Card

- Renders from the preview response only.
- Shows:
  - `preview_quality`
  - `warning_count_by_level`
  - `status`
  - `degraded_copy` when present
- The summary card must name degraded preview behavior exactly as returned by the backend.

### 3. Metric Panels

- Renders `metric_delta[]`.
- Each panel shows:
  - `display_label`
  - `baseline_value`
  - `candidate_value`
  - `delta`
  - `delta_pct`
  - `unit`
  - `direction`
- Metric ordering may follow backend array order or an explicit design grouping, but the frontend must not recompute metric values.

### 4. Warning Hierarchy Rail

- Renders `warnings[]` in backend order.
- Each row shows:
  - `level`
  - `message`
  - `impact_summary`
  - `parameter_key` when present
  - `metric_key` when present
- Use `warning_count_by_level` for summary chips only. Do not derive the hierarchy from the array contents.

### 5. Control Diff Panel

- Renders `control_diff[]`.
- Each changed row shows:
  - `display_label`
  - `parameter_key`
  - `previous_value`
  - `new_value`
  - `unit`
  - `last_modified_at`
- The compare page must not re-fetch TW-02 or join patch history to recreate these rows.

### 6. Refresh Action

- Submission target: `POST /api/v1/trainer/sessions/{session_id}/preview`
- Request body:
  - `refresh_mode: "manual"`
- The refresh CTA is visible only when `allowedActions.canRefreshPreview === true`.
- While `status = "pending"`, suppress or disable the refresh CTA and use the polling contract instead of repeated refresh calls.

## State Handling

| State | Required behavior |
|---|---|
| `status = "complete"` and surface `ok` | show full compare page, subject to `allowedActions.canRefreshPreview` |
| `status = "pending"` | show compare header and pending summary; metric panels may be replaced by loading placeholders; start polling using backend timing |
| `status = "failed"` | show compare header, failure copy from `degraded_copy`, and any backend-supplied control diff; do not show fake metric values |
| `status = "preview_unavailable"` | show canonical degraded-preview message; suppress refresh CTA; do not render fake metric panels |
| `allowedActions.canRefreshPreview = false` | hide refresh CTA even if the preview body is otherwise renderable |

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.trainer_preview = "ok"` | normal compare rendering |
| `meta.surfaces.trainer_preview = "stale"` | non-dismissable staleness banner; last-known compare result may remain visible; respect `allowedActions.canRefreshPreview` |
| `meta.surfaces.trainer_preview = "degraded"` | show degradation banner and backend-authored `degraded_copy`; suppress refresh CTA |
| `meta.surfaces.trainer_preview = "unavailable"` | replace metric panels and refresh controls with unavailable notice driven by backend copy |

The preview route owns degradation truth. Do not infer it from HTTP success or an empty `metric_delta[]` array.

## Polling Rules

- Poll only `GET /api/v1/trainer/sessions/{session_id}/preview?eval_id={eval_id}`.
- Poll only while:
  - `status = "pending"`
  - `polling.enabled = true`
  - current time is before `polling.deadline_at`
- Use exactly `polling.poll_interval_ms` between polls.
- Stop polling immediately when the status resolves or the surface becomes `degraded` / `unavailable`.
- Do not add optimistic metric updates or custom backoff logic.

## Constraints

- Use the dedicated preview route family only. Do not reconstruct compare state from TW-01 session detail, TW-02 patch responses, or local sandbox data.
- Do not derive metric deltas, warning levels, preview quality, or degraded copy client-side.
- Do not treat `preview_unavailable` as equivalent to `pending`.
- Do not keep polling after `polling.deadline_at`.
- Do not render refresh controls when `allowedActions.canRefreshPreview` is absent or false.
- If the live payload diverges from the synced contract, emit a `bff-gap` handoff instead of inventing fallback state.
- If any required field is missing, emit a `bff-gap` handoff instead of inventing a fallback.

## Acceptance

- The page renders backend-owned `metric_delta[]`, `warnings[]`, and `control_diff[]`.
- Warning hierarchy is driven by backend-authored `level` values and summary counts.
- Pending previews follow the published polling contract without duplicate refresh calls.
- `preview_unavailable` renders the canonical degraded-preview branch instead of a fake chart or empty state.
- Refresh CTA visibility follows `allowedActions.canRefreshPreview`, not local heuristics.
- Degradation behavior follows the published `meta.surfaces.trainer_preview` rules.

## References

- BFF contract: `docs/bff/TW-03-before-after-compare.md`
- Example payload: `docs/examples/TW-03-before-after-compare.json`
- Frontend handoff: `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md`
- Contract-ready coordination: `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- Frontend SA: `docs/lovable/PANTHEON_FRONTEND_SA.md`

# TW-03 Before/After Compare — Frontend Change Spec

## Overview

TW-03 is the production compare surface for trainer-session candidate previews.
Operators inspect backend-authored metric deltas, warning hierarchy, control
diff context, and rapid-eval preview state for one trainer session, then
request a manual preview refresh only when the BFF authorizes it.

The frontend must use only the TW-03 preview route family and backend-owned
authority, degradation, and polling semantics. No client-side preview math,
warning severity inference, control-diff reconstruction, or refresh heuristics
are permitted.

Feature ID: `TW-03-before-after-compare`  
Screen slug: `before-after-compare`  
Screen ID: `screen-before-after-compare`  
BFF contract: `docs/bff/TW-03-before-after-compare.md`  
Example payload: `docs/examples/TW-03-before-after-compare.json`  
Screen spec: `docs/screens/TW-03-before-after-compare.md`

---

## Allowed APIs

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/trainer/sessions/{session_id}/preview` | Read the latest compare preview or poll one specific `eval_id` |
| `POST` | `/api/v1/trainer/sessions/{session_id}/preview` | Request a manual rapid-eval refresh with `refresh_mode = "manual"` |

Refresh calls must go through the existing BFF client only. Do not issue raw
`fetch` calls from UI components.

---

## Required UI Modules

| Module | Route | Source of truth |
|---|---|---|
| Compare header | `/trainer/sessions/:session_id/compare` | `GET /api/v1/trainer/sessions/{session_id}/preview` |
| Rapid-eval summary card | `/trainer/sessions/:session_id/compare` | `status`, `preview_quality`, `warning_count_by_level`, `degraded_copy`, `meta.snapshot_at` |
| Metric delta panels | `/trainer/sessions/:session_id/compare` | `metric_delta[]` from preview response |
| Warning hierarchy rail | `/trainer/sessions/:session_id/compare` | `warnings[]` and `warning_count_by_level` from preview response |
| Control diff panel | `/trainer/sessions/:session_id/compare` | `control_diff[]` from preview response |
| Refresh CTA | `/trainer/sessions/:session_id/compare` | `allowedActions.canRefreshPreview` from preview response |

---

## State Rules

- Preview `status` (`complete` / `pending` / `failed` / `preview_unavailable`)
  is the compare lifecycle for the preview surface, not the trainer session
  lifecycle.
- Refresh CTA visibility and disabled state come only from
  `allowedActions.canRefreshPreview`. Do not infer refresh authority from
  session status, surface freshness, or metric presence.
- Degradation state comes only from `meta.surfaces.trainer_preview`.
- `preview_quality` is backend-owned. Do not infer it from `warnings[]`,
  `metric_delta[]`, coverage assumptions, or local heuristics.
- `warning_count_by_level` is summary truth, but warning severity still comes
  from each backend-authored `warnings[].level` value. Do not recalculate
  warning levels in the browser.

---

## Required Fields

### Compare header

- `session_id`, `status`, `eval_id`
- `baseline_snapshot_at`, `candidate_snapshot_at`
- `meta.snapshot_at`

### Summary card

- `preview_quality`
- `warning_count_by_level.critical`
- `warning_count_by_level.high`
- `warning_count_by_level.medium`
- `warning_count_by_level.informational`
- `degraded_copy`

### Metric delta panels

- `metric_delta[].metric_key`
- `metric_delta[].display_label`
- `metric_delta[].baseline_value`
- `metric_delta[].candidate_value`
- `metric_delta[].delta`
- `metric_delta[].delta_pct`
- `metric_delta[].unit`
- `metric_delta[].direction`

### Warning hierarchy rail

- `warnings[].warning_id`
- `warnings[].warning_code`
- `warnings[].level`
- `warnings[].parameter_key`
- `warnings[].metric_key`
- `warnings[].message`
- `warnings[].impact_summary`

### Control diff panel

- `control_diff[].control_id`
- `control_diff[].parameter_key`
- `control_diff[].display_label`
- `control_diff[].previous_value`
- `control_diff[].new_value`
- `control_diff[].unit`
- `control_diff[].last_modified_at`

### Authority and degradation fields

- `allowedActions.canRefreshPreview`
- `polling.enabled`
- `polling.poll_interval_ms`
- `polling.max_wait_ms`
- `polling.deadline_at`
- `meta.surfaces.trainer_preview`

---

## Failure Rules

If any required field listed above is absent from the BFF response:

1. Emit the TW-03 bff-gap handoff (`.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml`).
2. Stop rendering the affected surface.
3. Do not mock metric panels, warning ladders, control diffs, degraded copy, or polling timing.

---

## Degradation Rules

| `meta.surfaces.trainer_preview` | UI behaviour |
|---|---|
| `ok` | render normally |
| `stale` | show last-known compare result with non-dismissable staleness banner; refresh CTA still depends on `allowedActions` |
| `degraded` | show canonical PKT-005 degradation banner plus backend-authored `degraded_copy`; suppress refresh CTA |
| `unavailable` | suppress metric panels and refresh CTA; show only the backend-authored unavailable message |

Do not treat an empty `metric_delta[]` array as authoritative when the surface is
`stale`, `degraded`, or `unavailable`.

---

## Polling Contract

- Poll only `GET /api/v1/trainer/sessions/{session_id}/preview?eval_id={eval_id}`.
- Poll only while `status = "pending"` and `polling.enabled = true`.
- Use exactly `polling.poll_interval_ms` between polls.
- Stop polling immediately when:
  - `status != "pending"`
  - `meta.surfaces.trainer_preview` becomes `degraded` or `unavailable`
  - current time passes `polling.deadline_at`
- Do not poll the refresh `POST` route.
- Do not add optimistic metric updates, client-side timeout heuristics, or custom
  backoff logic.

---

## Completion Rules

On UI completion:

1. Emit the TW-03 ui-done handoff (`.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml`).
2. Publish the required feedback bundle:
   - `docs/pantheon-feedback/TW-03-before-after-compare/LOVABLE_CHANGE_FEEDBACK.md`
   - `docs/pantheon-feedback/TW-03-before-after-compare/API_GAP_REQUESTS.json`
   - `docs/pantheon-feedback/TW-03-before-after-compare/UI_DECISIONS.md`
   - `docs/pantheon-feedback/TW-03-before-after-compare/QA_STATUS.md`

---

## Non-Goals

- Do not derive compare metrics from TW-01 session detail, TW-02 patch responses,
  local backtests, or local simulation state.
- Do not derive warning levels from metric direction, message copy, or control
  proximity to an allowed range.
- Do not treat `preview_unavailable` as loading.
- Do not keep polling after `polling.deadline_at`.
- Do not surface refresh controls when `allowedActions.canRefreshPreview` is absent or false.
- Do not reconstruct control diffs from patch history when `control_diff[]` is missing.

---

## References

- BFF contract: `docs/bff/TW-03-before-after-compare.md`
- Screen spec: `docs/screens/TW-03-before-after-compare.md`
- Example payload: `docs/examples/TW-03-before-after-compare.json`
- Coordination task: `.coordination/responses/TW-03-before-after-compare-lovable-ui-task.yaml`
- Contract ready: `.coordination/responses/TW-03-before-after-compare-contract-ready.yaml`
- BFF-gap template: `.coordination/requests/TW-03-before-after-compare-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/TW-03-before-after-compare-ui-done.example.yaml`

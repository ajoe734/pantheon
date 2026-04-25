# TW-03 Before/After Compare — UI Decisions

## Decision log for implementation choices that deviate from or extend the screen spec.

### 1. Polling effect dependencies

The polling `useEffect` depends on `preview.status`, `preview.polling.enabled`,
`preview.eval_id`, `preview.meta.surfaces.trainer_preview`, `preview.polling.deadline_at`,
and `session_id`. This avoids stale closure issues while only restarting the interval when
the relevant state that controls polling decisions actually changes.

### 2. Surface state conflict resolution

When `status = "preview_unavailable"` AND `meta.surfaces.trainer_preview = "degraded"`,
the `preview_unavailable` status alert takes precedence in the UI ordering (rendered
separately from the surface banner). This matches the BFF contract where
`preview_unavailable` always comes with an empty `metric_delta[]` and the surface
being degraded or unavailable.

### 3. Warning count chips

`warning_count_by_level` chips are always rendered for all four hierarchy levels
(critical, high, medium, informational) even when a count is 0. This follows the BFF
contract requirement that `warning_count_by_level` always includes all four keys.

### 4. Metric delta ordering

`metric_delta[]` items are rendered in backend array order with no client-side
re-sorting. Per the screen spec, the frontend must not recompute metric values.

### 5. Refresh CTA suppression

The refresh CTA is suppressed (not just disabled) when `allowedActions.canRefreshPreview`
is false OR when the surface is degraded or unavailable. This matches the BFF contract
that says `canRefreshPreview` must be false in those states, but the UI adds an explicit
surface-level guard as a double-check.

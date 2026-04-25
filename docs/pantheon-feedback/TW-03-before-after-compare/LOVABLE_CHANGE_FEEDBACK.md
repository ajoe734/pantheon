# TW-03 Before/After Compare — Lovable Change Feedback

Reviewed the TW-03 Before/After Compare implementation in `ajoe734/front-ai-trading-system`
on branch `pkt-004-detail-fix`. This cycle activates the live Pantheon preview route family
and publishes the completed frontend handoff bundle.

## Outcome

Pantheon review result: accepted for handoff.

The Trainer Workbench before/after compare screen now runs against the published TW-03 BFF
preview route family only:

- `GET /api/v1/trainer/sessions/{session_id}/preview`
- `POST /api/v1/trainer/sessions/{session_id}/preview`

No client-side metric computation, warning severity inference, control-diff reconstruction,
or polling heuristics were introduced.

## Verified Against Pantheon

- `src/pages/trainer/TrainerBeforeAfterCompare.tsx` renders `session_id`, `status`,
  `eval_id`, `baseline_snapshot_at`, `candidate_snapshot_at`, and `meta.snapshot_at`
  in the compare header from the live preview response.
- Metric delta panels render `metric_delta[]` from the backend response in backend array
  order. Delta values, directions, and units come from `metric_delta[].delta`,
  `delta_pct`, `unit`, and `direction` without local recomputation.
- Warning hierarchy rail renders `warnings[]` in backend-returned order, using
  backend-authored `level` values for badges. `warning_count_by_level` is used only
  for the summary chip row.
- Control diff panel renders `control_diff[]` directly from the preview response without
  re-fetching TW-02 or joining patch history.
- Refresh CTA is visible only when `allowedActions.canRefreshPreview === true`. POST is
  called with `{ refresh_mode: "manual" }` only.
- Polling starts only when `status = "pending"` and `polling.enabled = true`. Interval
  is exactly `polling.poll_interval_ms`. Polling stops when status resolves,
  `meta.surfaces.trainer_preview` becomes degraded or unavailable, or current time passes
  `polling.deadline_at`.
- `preview_unavailable` renders the backend-authored `degraded_copy` message and suppresses
  metric panels and refresh CTA — it is not treated as a loading state.
- BFF contract gap detection validates all required fields on every response. Missing
  fields trigger the canonical gap alert with the bff-gap handoff path instead of a local
  fallback.

## Surface Degradation

All four surface states are handled correctly:
- `ok`: normal compare rendering
- `stale`: non-dismissable staleness banner; last-known compare result stays visible;
  `allowedActions.canRefreshPreview` governs CTA
- `degraded`: degradation banner shown; refresh CTA suppressed
- `unavailable`: metric panels and refresh CTA replaced by unavailable notice from backend copy

## No Open API Gaps

The live preview route returned all required fields for the complete TW-03 contract.
No bff-gap handoff is needed from this pass.

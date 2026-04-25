# TW-03 Before/After Compare Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the returned TW-03 `ui-done` and `frontend-feedback`
handoffs against the published Before/After Compare contract, canonical
frontend change spec, sibling front implementation, and the current Pantheon
workspace.

The reviewed compare implementation is now materially aligned to the TW-03
packet:

- it stays on `GET` and `POST /api/v1/trainer/sessions/{session_id}/preview`
  only
- the reviewed source commit
  `ed8db5db794202659c5a377d2939df580585ccbb` now contains
  `src/pages/trainer/replayContract.ts`
- `validatePreviewResponse()` now fail-closes on the required TW-03 per-item
  `metric_delta[]`, `warnings[]`, `control_diff[]`, and `degraded_copy` fields
- the reviewed source commit is Git-visible on `origin/pkt-004-detail-fix`
- Pantheon's latest review record found no new TW-03 endpoint or contract gap

The loop remains `followup-required` because the front-owned transport is still
not replay-clean:

1. immutable source commit `ed8db5db794202659c5a377d2939df580585ccbb` still
   stores older TW-03 request bodies pointing at
   `d1fe9917deef22cfd0c656e1210eff06abd1cd83`
2. remote tip `dbc4a16dc0e9f0b8d33e1576908341ea056c660d` only repoints the two
   request files to `ed8db5db794202659c5a377d2939df580585ccbb`
3. the republished machine summary still omits
   `src/pages/trainer/replayContract.ts` from `changed_files`

Under Pantheon's replay rules, the request pair and feedback bundle must
resolve from one truthful front transport commit before this packet can close.

## Verified Pantheon Contract

- `GET /api/v1/trainer/sessions/{session_id}/preview`
- `POST /api/v1/trainer/sessions/{session_id}/preview`

No new endpoint, no shadow state, and no client-side preview reconstruction is
authorized in this follow-up cycle.

## Verified UI Alignment

- `TrainerBeforeAfterCompare.tsx` remains on the dedicated TW-03 preview route
  family through the shared BFF client.
- Metric deltas still render from backend-owned `metric_delta[]` only.
- Warning hierarchy still renders from backend-owned `warnings[]` in backend
  order, with `warning_count_by_level` used only for summary chips.
- Control diffs still render from backend-owned `control_diff[]` without a
  TW-02 re-fetch or local reconstruction.
- Refresh authority still depends only on
  `allowedActions.canRefreshPreview`, and `POST /preview` remains manual only.
- Pending polling still uses backend-owned `eval_id` and
  `polling.poll_interval_ms` only.
- `preview_unavailable` and degraded states still render backend-owned
  `degraded_copy` rather than local fallback copy.

## Findings Requiring Another Cycle

### 1. The advertised source commit is still not the truthful TW-03 replay target

Pantheon re-verified:

- `git -C ../front-ai-trading-system show ed8db5db794202659c5a377d2939df580585ccbb:.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
- the paired `frontend-feedback` request at that same commit

Both immutable request files still embed:

- `source_commit: d1fe9917deef22cfd0c656e1210eff06abd1cd83`

So `ed8db5db794202659c5a377d2939df580585ccbb` is no longer missing code or
build integrity, but it still is not the truthful transport anchor for the
final published TW-03 request pair.

### 2. The current remote tip only repoints metadata

Pantheon re-verified:

- `git -C ../front-ai-trading-system show dbc4a16dc0e9f0b8d33e1576908341ea056c660d --stat --oneline -- .coordination/requests/TW-03-before-after-compare-ui-done.yaml .coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml`

Observed result:

- only the two TW-03 request files changed
- the change only repoints their embedded `source_commit` to
  `ed8db5db794202659c5a377d2939df580585ccbb`

That means the remote publish commit is a metadata repoint, not the truthful
TW-03 transport tree itself.

### 3. The republished machine summary still under-reports the shipped bundle

The current TW-03 request summary still lists:

- `src/pages/trainer/TrainerBeforeAfterCompare.tsx`
- `src/pages/trainer/types.ts`
- `src/lib/bffClient.ts`
- `src/App.tsx`

but still omits:

- `src/pages/trainer/replayContract.ts`

Pantheon cannot treat that machine summary as transport-truthful until it is
refreshed alongside the replay-clean request pair.

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon endpoint family: unchanged
- Pantheon API gap: none in this cycle
- Front follow-up required:
  - publish one Git-visible front transport commit containing the canonical
    TW-03 request pair, feedback bundle, compare route wiring, reviewed screen,
    and `src/pages/trainer/replayContract.ts`
  - point both request bodies at that same truthful `source_commit`
  - refresh the request-pair machine summary so `changed_files` matches the
    shipped bundle
  - redispatch Pantheon review on the unchanged TW-03 contract after the
    replay-clean publication exists

## Verification Performed

- Reviewed the current Pantheon TW-03 review artifacts:
  - `.coordination/reviews/TW-03-before-after-compare-review.md`
  - `.coordination/responses/TW-03-before-after-compare-frontend-feedback.yaml`
- Re-verified the front transport anchors:
  - `git -C ../front-ai-trading-system show ed8db5db794202659c5a377d2939df580585ccbb:.coordination/requests/TW-03-before-after-compare-ui-done.yaml`
  - `git -C ../front-ai-trading-system show dbc4a16dc0e9f0b8d33e1576908341ea056c660d --stat --oneline -- .coordination/requests/TW-03-before-after-compare-ui-done.yaml .coordination/requests/TW-03-before-after-compare-frontend-feedback.yaml`
  - `git -C ../front-ai-trading-system branch -r --contains ed8db5db794202659c5a377d2939df580585ccbb`
  - `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
- Re-verified the committed dependency and validator hardening:
  - `git -C ../front-ai-trading-system cat-file -e ed8db5db794202659c5a377d2939df580585ccbb:src/pages/trainer/replayContract.ts`
  - `git -C ../front-ai-trading-system show ed8db5db794202659c5a377d2939df580585ccbb:src/pages/trainer/TrainerBeforeAfterCompare.tsx | rg -n "validatePreviewResponse|warning_id|warning_code|control_id|degraded_copy|metric_key|delta_pct"`

## Not Completed

- No new front-repo publication was created from Pantheon in this cycle
- No live browser QA against a deployed Pantheon environment was performed in
  this cycle
- No new Pantheon backend or runtime change was needed for TW-03

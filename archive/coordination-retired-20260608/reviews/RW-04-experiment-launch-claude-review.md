# RW-04 Experiment Launch — Claude Review

## Date

2026-04-21

## Reviewer

Claude

## Review Basis

This review was conducted after auto-reassignment from Copilot (quota terminal: 402). It independently reads the prior Codex review findings and verifies them directly against the live working-tree files in `../front-ai-trading-system`.

## Findings

### 1. High: RW-04 implementation is not in any Git-visible commit

- `git -C ../front-ai-trading-system log --oneline -5` shows latest commit is
  `93a4b58 EXEC-FRONT-PKT003-001 republish PKT-003 replay metadata` — the RW-04
  pages are absent from that tree.
- `ExperimentLaunch.tsx`, `ExperimentDetail.tsx`, `ExperimentHistory.tsx`,
  `ExperimentRunView.tsx`, `ExperimentRunHelpers.ts`, `ExperimentTypes.ts` all
  exist only as untracked working-tree files.
- `ui-done.yaml` `source_commit: 93a4b58891031442133a6966d0354ae216a80b72`
  does not contain these files.
- The delivery loop cannot close until a canonical, replay-clean commit exists
  in the front repo that contains all RW-04 implementation files plus the
  `ui-done.yaml` and `frontend-feedback` coordination requests.

### 2. High: Active BFF runtime exposes no `/api/v1/experiments*` routes

- `GET /health` → 200, but `GET /api/v1/experiments?page_size=5` → 404 and
  `POST /api/v1/experiments/launch` → 404.
- Confirmed by `needs-runtime.yaml`: "running operator-bff service at
  http://127.0.0.1:18001 returns 200 for /health yet 404 for both endpoints."
- Pantheon-owned blocker. Live HTTP acceptance cannot be verified until the
  operator-bff is refreshed.

### 3. Medium: `isExperimentRouteNotLive` collapses contract-defined 404 into route-not-live

- `ExperimentRunHelpers.ts:30`:
  ```ts
  return error.status === 404 || EXPERIMENT_ROUTE_NOT_LIVE_CODES.has(error.code);
  ```
- Any BFF-returned 404 (including contract-defined `OBJECT_NOT_FOUND` for a
  missing `experiment_id`) is rendered as "Routes not yet live" instead of
  surfacing the object-missing state.
- The same flawed helper is used by `ExperimentDetail.tsx:62-74` and
  `ExperimentLaunch.tsx:182-194`.
- Fix: distinguish route-absence (404 + route-not-found error codes) from
  object-level 404s by checking `error.code` before `error.status`.

### 4. Medium: Polling stops when the first post-launch detail read fails before a durable snapshot arrives

- `ExperimentLaunch.tsx:212-227` — the polling `useEffect` guard:
  ```ts
  if (!detail || detailContractGap.length > 0 || ...) { return; }
  ```
  If the first post-launch detail fetch returns a contract gap or a non-route
  error, `detail` remains `null`, the early return fires, and no timer is
  armed. Subsequent refreshes via `detailRefreshKey` never trigger.
- `detailError` and `detailContractGap` are only visible inside
  `ExperimentRunView`, which is gated on `detail !== null`. The operator sees
  a passive "Waiting for durable status" alert with no retry.
- Fix: arm a retry timer (or show an explicit retry CTA) when `detailError` or
  `detailContractGap` is set, so the polling flow continues rather than
  silently stalling.

### 5. Medium: History paginator preserves stale page tokens when `ticket_id` filter changes

- `ExperimentHistory.tsx:375-383` — status filter change calls `setPageHistory([])`.
- `ExperimentHistory.tsx:358-365` — `ticket_id` input change does not call
  `setPageHistory([])`.
- After paging through runs for one ticket and then changing `ticket_id`, the
  `Previous` button reuses tokens from the prior filter context.
- Fix: add `setPageHistory([])` to the ticket_id `onChange` handler alongside
  the `updateParams` call.

## Verified Positives

- All three RW-04 routes are mounted in `App.tsx`:
  `/research/experiments`, `/research/experiments/launch`,
  `/research/experiments/:experiment_id`.
- No raw `fetch` or `axios` calls in component files; all BFF traffic routes
  through `rw04ExperimentApi` in `src/lib/bffClient.ts`.
- Cancel CTA is gated on `allowedActions.canCancel === true` AND surface not
  `"unavailable"` or `"degraded"` — matches spec exactly.
- `allowedActions.canCancel` is not inferred from `status`, elapsed time, or
  `artifact_ids`.
- History degradation banners (`stale`, `degraded`, `unavailable`) are rendered
  from `meta.surfaces.experiment_history` values — not synthesized client-side.
- `artifact_ids[]` is rendered as a ledger only; no speculative artifact links
  are constructed.
- Terminal states (`completed`, `failed`, `canceled`) correctly stop polling.
- Sibling front ESLint, `tsc --noEmit`, and `npm run build` all passed for the
  reviewed files (per prior review evidence — locally accessible files).

## Decision

`reopen` — return to Codex with the three required frontend fixes and the
commit / runtime requirements.

The implementation is architecturally sound and broadly aligned with the
published RW-04 contract, but three exploitable contract-handling bugs exist in
the frontend code and the delivery artifact is not replay-clean. All five issues
below must be resolved before this review can advance to `review_approved`.

## Required Changes

1. **Front repo (High):** Publish one Git-visible commit in `front-ai-trading-system`
   containing all RW-04 implementation files, the `ui-done.yaml`, and the
   `frontend-feedback` coordination request. Point both request bodies at that
   exact immutable SHA.

2. **Pantheon runtime (High):** Refresh or redeploy the operator-bff so all
   four RW-04 routes appear in the live OpenAPI document and return published
   field shapes under operator auth.

3. **Frontend bug fix (Medium):** `isExperimentRouteNotLive` must not collapse
   all HTTP 404s into a route-not-live condition. Only classify as route-not-live
   when `error.code` is in `EXPERIMENT_ROUTE_NOT_LIVE_CODES`; fall through to
   the object-missing error path for all other 404s.

4. **Frontend bug fix (Medium):** Arm a polling retry (or surface an explicit
   retry CTA) in the launch status view when `detailError` or `detailContractGap`
   is set, so the durable status flow continues instead of silently stalling
   after the first failed post-launch detail read.

5. **Frontend bug fix (Medium):** Add `setPageHistory([])` to the `ticket_id`
   filter `onChange` handler in `ExperimentHistory.tsx` so the pagination
   backstack is reset whenever the filter context changes.

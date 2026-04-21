# RW-04 Experiment Launch Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/RW-04-experiment-launch-ui-done.yaml` plus the paired
frontend-feedback response against the current RW-04 contract, canonical
example payload, coordination replay rules, the sibling front implementation,
the live operator-bff runtime, and a workspace-backed unavailable probe.

The RW-04 route family is now verified over truthful HTTP:

- `POST /api/v1/experiments/launch`
- `GET /api/v1/experiments`
- `GET /api/v1/experiments/{experiment_id}`
- `POST /api/v1/experiments/{experiment_id}/cancel`

Targeted RW-04 contract tests pass, `/openapi.json` on
`http://127.0.0.1:18001` advertises all four routes, authenticated live probes
return truthful queued, running, completed, failed, canceled, degraded, and
`OBJECT_NOT_FOUND` behavior, and a workspace-backed probe on
`http://127.0.0.1:18012` returns truthful unavailable list/detail envelopes.

The reviewed front working tree is also contract-aligned now: the detail 404
branch preserves `OBJECT_NOT_FOUND`, the launch status view keeps retrying
after an initial detail-read failure, history filter changes reset pagination
backstack, and the targeted sibling front eslint, TypeScript, and production
build checks pass.

Pantheon does not need a new RW-04 endpoint or any shadow-state workaround in
this cycle. Pantheon's runtime follow-up is complete. The remaining blocker is
front-owned publication replay only.

## Delivered Findings

### 1. Pantheon RW-04 routes are live and contract-shaped over real HTTP

Published RW-04 contract:

- `POST /api/v1/experiments/launch`
- `GET /api/v1/experiments`
- `GET /api/v1/experiments/{experiment_id}`
- `POST /api/v1/experiments/{experiment_id}/cancel`

Observed live runtime results at `http://127.0.0.1:18001`:

- `GET /openapi.json` advertises the full RW-04 route family
- authenticated `GET /api/v1/experiments?page_size=5` returns `200 OK` with
  `meta.surfaces.experiment_history = degraded`
- authenticated `GET /api/v1/experiments/exp-20260419-012` returns a completed
  payload with `allowedActions.canCancel = false`
- authenticated `GET /api/v1/experiments/exp-20260418-009` returns a running
  payload with `allowedActions.canCancel = true`
- authenticated `GET /api/v1/experiments/exp-20260417-004` returns a failed
  payload with populated `failure.reason_code` and `failure.message`
- authenticated `GET /api/v1/experiments/does-not-exist` returns
  `404 OBJECT_NOT_FOUND`
- authenticated `POST /api/v1/experiments/launch` returned queued
  `exp-20260421-004`; authenticated
  `POST /api/v1/experiments/exp-20260421-004/cancel` returned
  `status = canceled` with `allowedActions.canCancel = false`; follow-up
  detail/list reads confirmed the durable canceled state

Observed unavailable verification at `http://127.0.0.1:18012`:

- list route returns `200 OK`, `data = []`, and
  `meta.surfaces.experiment_history = unavailable`
- detail route returns `200 OK`,
  `meta.surfaces.experiment_status = unavailable`, while preserving the
  backend-owned experiment snapshot

Observed automated verification:

- `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
- Result: `21 passed`

Impact:

- Pantheon's live HTTP route publication is no longer the blocker for RW-04
- the resolved runtime follow-up in
  `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml` can stay
  completed

### 2. The reviewed RW-04 front working tree is now contract-aligned

Observed front behavior in the sibling working tree:

- `ExperimentLaunch.tsx` keeps polling after an initial failed detail read and
  exposes a retry path while waiting for the first durable snapshot
- `ExperimentRunHelpers.ts` only classifies route-not-live for
  `404 NOT_FOUND` or `404 ROUTE_NOT_FOUND`, preserving
  `404 OBJECT_NOT_FOUND` from the live detail route
- `ExperimentHistory.tsx` clears `pageHistory` when either `ticket_id` or
  `status` changes
- `ExperimentRunView.tsx` still gates cancel rendering on
  `allowedActions.canCancel` plus surface degradation semantics only
- the reviewed front app still mounts:
  - `/research/experiments`
  - `/research/experiments/launch`
  - `/research/experiments/:experiment_id`

Observed sibling front verification:

- targeted eslint for the RW-04 slice passed
- `npx tsc --noEmit` passed
- `npm run build` passed with only the existing non-blocking Browserslist age
  notice and Vite chunk-size warning

Impact:

- the earlier front-owned RW-04 correctness findings are resolved in the
  current working tree
- Pantheon's remaining RW-04 blocker is publication truth, not UI behavior

### 3. The Git-visible front publication is still incomplete

Current front publication state:

- both returned request bodies still advertise `source_commit: HEAD`
- sibling front `HEAD` is
  `93a4b58891031442133a6966d0354ae216a80b72` on branch
  `pkt-004-detail-fix`
- `git -C ../front-ai-trading-system ls-tree -r --name-only HEAD -- ...`
  shows only:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`
  - `src/lib/bffClient.ts`
- `git -C ../front-ai-trading-system status --short -- ...` still shows the
  canonical request pair, feedback bundle, and RW-04 experiment pages as
  working-tree-only artifacts

Impact:

- replay still cannot reconstruct the reviewed RW-04 cycle from a truthful
  immutable front commit tuple
- supervisor-visible closeout still cannot proceed until the front repo
  republishes the request pair, feedback bundle, and reviewed UI files from
  one Git-visible commit and points both request bodies at that exact SHA

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Published endpoint family: live and verified over real HTTP
- Pantheon delivery completed:
  - revalidated the full RW-04 route family through live authenticated HTTP
  - confirmed truthful unavailable RW-04 list/detail envelopes through a
    workspace-backed HTTP probe
  - reran the targeted RW-04 contract slice (`21 passed`)
  - confirmed the current sibling RW-04 working tree matches the published
    contract and acceptance rules
- Front follow-up still required:
  - publish the canonical `ui-done` request, canonical `frontend-feedback`
    request, feedback bundle, and reviewed RW-04 UI files from one immutable
    front commit
  - replace `source_commit: HEAD` with that exact immutable publication SHA in
    both returned request bodies
  - run deployed browser QA against `/research/experiments`,
    `/research/experiments/launch`, and
    `/research/experiments/:experiment_id` as non-blocking confirmation after
    publication
- Current loop outcome: `delivered` on the Pantheon backend-delivery record;
  packet replay remains front-blocked until the canonical front publication
  tuple is truthful

## Verification Performed

- Reviewed the returned Pantheon-local request and response artifacts:
  - `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  - `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`
- Reviewed the canonical packet:
  - `docs/bff/RW-04-experiment-launch.md`
  - `docs/examples/RW-04-experiment-launch.json`
  - `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `src/pages/research/ExperimentLaunch.tsx`
  - `src/pages/research/ExperimentDetail.tsx`
  - `src/pages/research/ExperimentHistory.tsx`
  - `src/pages/research/ExperimentRunHelpers.ts`
  - `src/pages/research/ExperimentRunView.tsx`
  - `src/pages/research/ExperimentTypes.ts`
  - `src/lib/bffClient.ts`
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`
- Ran sibling front verification:
  - targeted eslint for the RW-04 slice
  - `npx tsc --noEmit`
  - `npm run build`
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
- Probed the live operator-bff runtime:
  - `GET http://127.0.0.1:18001/openapi.json`
  - authenticated `GET http://127.0.0.1:18001/api/v1/experiments?page_size=5`
  - authenticated `GET http://127.0.0.1:18001/api/v1/experiments/exp-20260419-012`
  - authenticated `GET http://127.0.0.1:18001/api/v1/experiments/exp-20260418-009`
  - authenticated `GET http://127.0.0.1:18001/api/v1/experiments/exp-20260417-004`
  - authenticated `GET http://127.0.0.1:18001/api/v1/experiments/does-not-exist`
  - authenticated `POST http://127.0.0.1:18001/api/v1/experiments/launch`
  - authenticated
    `POST http://127.0.0.1:18001/api/v1/experiments/exp-20260421-004/cancel`
- Verified unavailable behavior through a workspace-backed probe:
  - `GET http://127.0.0.1:18012/api/v1/experiments?page_size=5`
  - `GET http://127.0.0.1:18012/api/v1/experiments/exp-20260418-009`
- Re-checked sibling front publication truth:
  - `git -C ../front-ai-trading-system rev-parse HEAD`
  - `git -C ../front-ai-trading-system ls-tree -r --name-only HEAD -- ...`
  - `git -C ../front-ai-trading-system status --short -- ...`

## Not Completed

- No deployed browser QA against a shared Pantheon environment was performed in
  this review cycle
- The front repo still has not published one immutable Git-visible commit that
  contains the RW-04 request pair, feedback bundle, and reviewed UI files

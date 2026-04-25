# PKT-001 Deployment Review Backend Delivery Note

## Status

`front-followup-required`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-001-deployment-review-ui-done.yaml` against the
current PKT-001 contract, example payload, coordination replay rules, the
Git-visible front implementation on `origin/main`, and the local Pantheon BFF
workspace.

The core PKT-001 read surface remains live in the current workspace:

- Pantheon serves `GET /api/v1/operator/deployment-plans`
- Pantheon serves `GET /api/v1/operator/deployment-review/{plan_id}`
- targeted PKT-001 contract tests pass
- the reviewed front route wiring remains on `/operator/deployment-review` and
  `/operator/deployment-plans/:planId`

No new endpoint, contract expansion, or client-side shadow state is authorized
in this cycle. Runtime SSE remains the approved PKT-005 incremental decoration
only.

The loop still cannot close because the current PKT-001 handoff is not yet a
truthful closed-loop front request pair:

1. `origin/main` publishes `.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
   and the claimed PKT-001 UI files, but does not publish the paired
   `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml`
   or the required `docs/pantheon-feedback/PKT-001-deployment-review/` bundle
2. the current list and detail readers still do not fail closed on the
   required PKT-001 `meta.surfaces` key sets
3. the published `ui-done` request still advertises `source_commit: HEAD`
   instead of an immutable commit SHA

## Contract State

Pantheon continues to serve the published PKT-001 route family:

- `GET /api/v1/operator/deployment-plans`
- `GET /api/v1/operator/deployment-review/{plan_id}`
- `POST /api/v1/operator/commands`

The delivered read contract still includes:

- list `items / page_info / meta`
- `meta.surfaces.deployment_plans`
- `meta.surfaces.allowedActions`
- detail `meta.surfaces.deployment_plan`
- detail `meta.surfaces.approval_decision`
- detail `allowedActions.canApprove`
- detail `allowedActions.canReject`
- detail `allowedActions.canPromoteToPaper`
- detail `meta.surfaces.latestRun`
- detail `meta.surfaces.review`
- detail `meta.surfaces.runtime_binding`
- degradation metadata when any list or detail surface is not healthy

## Remaining Front Follow-up

- Publish the canonical `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml`
  request plus the required `docs/pantheon-feedback/PKT-001-deployment-review/`
  bundle from the front repo.
- Republish `.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
  from that same immutable front commit and replace `source_commit: HEAD` with
  the exact publication SHA.
- Update `src/pages/operator/DeploymentReviewConsole.tsx` to require
  `meta.surfaces.deployment_plans` and `meta.surfaces.allowedActions` through
  the shared degradation helper instead of only checking that
  `meta.surfaces` exists.
- Update `src/pages/operator/DeploymentPlanDetail.tsx` to require
  `meta.surfaces.deployment_plan`, `approval_decision`, `allowedActions`,
  `latestRun`, `review`, and `runtime_binding` through the same helper.
- Redispatch Pantheon review on the unchanged contract after the replay-clean
  request pair and fail-closed surface validation land.

## Boundary Decision

- Runtime live updates for Deployment Review remain inherited from the existing
  `PKT-005` SSE substrate at
  `GET /api/v1/runtime/{runtime_id}/events/stream`.
- This SSE endpoint is incremental-only cross-cut infrastructure, not a new
  PKT-001 snapshot route.
- The source of truth for list data, detail data, degradation banners, and CTA
  authority remains the PKT-001 snapshot responses.

## Delivered Findings

### 1. Pantheon PKT-001 route family remains live and contract-shaped

Published PKT-001 routes:

- `GET /api/v1/operator/deployment-plans`
- `GET /api/v1/operator/deployment-review/{plan_id}`

Observed automated verification:

- `python3 -m pytest -q services/control-plane/bff/test_pkt001_deployment_review_console_contract.py`
- Result: `3 passed in 9.93s`

Impact:

- the reviewed screen can continue to use the published PKT-001 read surfaces
  without any new Pantheon API work
- the expected degradation banner surface keys are still present in the current
  Pantheon contract and regression slice

### 2. The current Git-visible front return still misses fail-closed surface validation

Observed front implementation on `origin/main`:

- `src/pages/operator/DeploymentReviewConsole.tsx` still treats
  `meta.surfaces` existence as sufficient and does not require
  `deployment_plans` plus `allowedActions`
- `src/pages/operator/DeploymentPlanDetail.tsx` still treats
  `meta.surfaces` existence as sufficient and does not require
  `deployment_plan`, `approval_decision`, `allowedActions`, `latestRun`,
  `review`, and `runtime_binding`
- the shared helper `src/lib/degradationBanner.ts:120-149` already exists and
  is the expected authority for this check

Impact:

- the current front UI can still accept partial PKT-001 surface maps instead
  of surfacing an explicit contract-gap state
- the remaining blocker is front-owned validation work, not Pantheon route
  availability

### 3. The Git-visible front publication is still incomplete for this cycle

Current front-repo state on `origin/main`:

- `.coordination/requests/PKT-001-deployment-review-ui-done.yaml` exists and
  still sets `source_commit: HEAD`
- `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml` is
  absent
- the claimed PKT-001 UI files are Git-visible on `origin/main`
- the inspected local source checkout is detached at
  `7f2fbbeefc988eb2ef30d1fed5edb0918ad5276f`, while the Git-visible branch head
  used for review is `5444be87c1eb52d9a622d3ff521d66ebf5631b43`

Impact:

- supervisor can detect the returned `ui-done` handoff, but the current PKT-001
  cycle is not replay-clean or fully reviewable from a commit-pinned front
  request pair
- the next front cycle must publish both the feedback bundle and the immutable
  request pair together

## Pantheon-Side Outcome

- Pantheon contract: unchanged in this review cycle
- Published endpoints: already live in the current workspace
- Pantheon delivery recorded:
  - mirrored the returned `ui-done` request into Pantheon coordination state
  - refreshed the PKT-001 review packet and delivery lock
  - published the backend-delivery response for the next front-owned cycle
- Front follow-up still required:
  - publish the canonical frontend-feedback request and feedback bundle
  - republish the ui-done request from the same immutable commit with a pinned
    `source_commit`
  - add fail-closed surface-key validation in the list and detail readers
- Current loop outcome: `followup-required` on the Pantheon backend-delivery
  record; packet loop remains open until the front publication tuple is
  truthful and the required PKT-001 surface keys fail closed

## Verification Performed

- Reviewed the returned front-owned request:
  - `/tmp/front-origin-main-verify/.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
- Reviewed the Git-visible front publication on `origin/main`:
  - `git -C /tmp/front-origin-main-verify show origin/main:.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
  - `git -C /tmp/front-origin-main-verify ls-tree -r --name-only origin/main -- .coordination/requests/PKT-001-deployment-review-ui-done.yaml .coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml src/lib/bffClient.ts src/pages/operator/DeploymentReviewConsole.tsx src/pages/operator/DeploymentPlanDetail.tsx src/pages/operator/DeploymentPlanDetailRoute.tsx src/App.tsx src/components/AppSidebar.tsx`
- Reviewed the canonical packet:
  - `docs/bff/PKT-001-deployment-review-console.md`
  - `docs/screens/PKT-001-deployment-review-console.md`
  - `docs/examples/PKT-001-deployment-review-console.json`
  - `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the Git-visible front implementation:
  - `git -C /tmp/front-origin-main-verify show origin/main:src/pages/operator/DeploymentReviewConsole.tsx`
  - `git -C /tmp/front-origin-main-verify show origin/main:src/pages/operator/DeploymentPlanDetail.tsx`
  - `git -C /tmp/front-origin-main-verify show origin/main:src/App.tsx`
  - `git -C /tmp/front-origin-main-verify show origin/main:src/components/AppSidebar.tsx`
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest -q services/control-plane/bff/test_pkt001_deployment_review_console_contract.py`
  - Result: `3 passed in 9.93s`

## Files Updated

- `.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
- `.coordination/responses/PKT-001-deployment-review-backend-delivery.yaml`
- `.coordination/responses/PKT-001-deployment-review-lovable-ui-task.yaml`
- `.coordination/reviews/PKT-001-deployment-review-review.md`
- `docs/pantheon-delivery/PKT-001-deployment-review/DELIVERY_NOTE.md`
- `docs/pantheon-delivery/PKT-001-deployment-review/CONTRACT_LOCK.json`

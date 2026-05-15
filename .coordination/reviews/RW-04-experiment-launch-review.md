# RW-04 Experiment Launch Review Packet

## Date

2026-04-21

## Reviewer

Codex

## Findings

None.

## Verified

- Codex revalidated the runtime-refresh acceptance on 2026-04-21 by rerunning
  `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
  (`21 passed`), checking live OpenAPI publication on `http://127.0.0.1:18001`,
  confirming authenticated degraded list/detail responses plus
  `404 OBJECT_NOT_FOUND`, replaying a live queued-to-canceled round-trip for
  `exp-20260421-005`, and launching an ephemeral
  `BFF_READ_SURFACE_STATE=unavailable` probe on `http://127.0.0.1:18012` to
  verify truthful unavailable list/detail envelopes.
- The immutable front publication chain is now replayable from Git history:
  `origin/pkt-004-detail-fix` points at `f00791b`, and the request pair now
  references wiring commit `f672af2c0019618ce05cf07c7ed50c65897e9fbb`
  in both handoffs
  (`../front-ai-trading-system/.coordination/requests/RW-04-experiment-launch-ui-done.yaml:5`,
  `../front-ai-trading-system/.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml:5`).
- The repointed handoffs truthfully include the app-shell and shared-client
  wiring in `changed_files`
  (`../front-ai-trading-system/.coordination/requests/RW-04-experiment-launch-ui-done.yaml:18-34`,
  `../front-ai-trading-system/.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml:23-39`).
- The previously missing wiring is present in immutable Git history:
  Research routes are mounted in
  `../front-ai-trading-system/src/App.tsx:39-41` and
  `../front-ai-trading-system/src/App.tsx:151-153`,
  Research navigation is exposed in
  `../front-ai-trading-system/src/components/AppSidebar.tsx:60-66`,
  breadcrumb mapping is present in
  `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx:16-18`,
  and the shared RW-04 client is defined in
  `../front-ai-trading-system/src/lib/bffClient.ts:1314-1346`.
- The three earlier logic regressions remain fixed:
  route-not-live detection distinguishes object-level 404s in
  `../front-ai-trading-system/src/pages/research/ExperimentRunHelpers.ts:28-36`,
  launch polling continues until unavailable/terminal detail instead of
  stalling in
  `../front-ai-trading-system/src/pages/research/ExperimentLaunch.tsx:212-236`,
  and history filter changes clear pagination state in
  `../front-ai-trading-system/src/pages/research/ExperimentHistory.tsx:357-367`.
- Cancel/degradation handling still follows the published contract:
  detail cancel visibility is derived from `allowedActions.canCancel` plus the
  status surface in
  `../front-ai-trading-system/src/pages/research/ExperimentRunView.tsx:91-105`
  and `../front-ai-trading-system/src/pages/research/ExperimentRunView.tsx:144-178`,
  with degraded-state suppression called out in
  `../front-ai-trading-system/src/pages/research/ExperimentRunView.tsx:466-474`;
  history stale/degraded/unavailable behavior remains explicit in
  `../front-ai-trading-system/src/pages/research/ExperimentHistory.tsx:438-470`.
- Static verification passed on the sibling front repo:
  `npm run build` succeeded on 2026-04-21 in `/home/lupin/code/front-ai-trading-system`.
- Live runtime verification also passed on 2026-04-21:
  `GET http://127.0.0.1:18001/openapi.json` returned `200` and listed all four
  `/api/v1/experiments*` routes, and unauthenticated
  `GET /api/v1/experiments?page_size=5` returned `401`, matching operator-auth
  gating.

## Decision

Approved. `RW-04-experiment-launch` is ready for `review_approved`.

The prior publication-truth blocker is resolved: the handoff pair now points at
an immutable commit that actually contains the route shell, navigation,
breadcrumb, and shared-client wiring it claims to deliver. The live runtime
route family is present, the previously reported frontend logic bugs remain
fixed, and the front repo still builds cleanly. All task acceptance criteria
are met.

## Residual Risk

- The front worktree contains unrelated concurrent changes outside the RW-04
  slice. They do not affect this review because the approved evidence is tied
  to the immutable `f672af2c`/`f00791b` publication chain, not to uncommitted
  workspace state.

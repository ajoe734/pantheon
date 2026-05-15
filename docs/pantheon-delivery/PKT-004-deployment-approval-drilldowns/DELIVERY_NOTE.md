# PKT-004 Deployment / Approval Drilldowns Backend Delivery Note

## Status

`loop-complete`

## Summary

Pantheon re-reviewed the current PKT-004 Deployment / Approval Drilldowns UI
cycle from the sibling `front-ai-trading-system` checkout against the published
contract and example payload.

The current front request pair at
`28b0bb8b281b5969af4be83850be525868ca11b3` now truthfully points at the
replay-clean implementation bundle
`0e93994eddfc8651e696191964c31c65c56c6201`. The UI remains inside the
published DP-01 through DP-04 boundary:

- the four read routes are unchanged and read-only
- list filters are forwarded as Pantheon query params
- governance actions remain cross-linked to PKT-001 surfaces instead of being
  reimplemented locally
- the request pair is Git-visible and replayable from the advertised source
  commit

Pantheon also closed one local acceptance drift in this review cycle: commit
`287a541774a3431522f815827c8dcf5ce7e71a4b` updates the seeded BFF fallback so
deployment-plan payloads include the required `plan_id` field during local
acceptance runs, and the PKT-004 regression now asserts that contract detail.

No additional front-end or Pantheon follow-up is required for the current
PKT-004 scope.

## Verified UI Alignment

- Reviewed the current front request artifacts:
  - `../front-ai-trading-system/.coordination/requests/PKT-004-deployment-approval-drilldowns-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-004-deployment-approval-drilldowns-frontend-feedback.yaml`
- Verified the request pair advertises
  `source_commit: 0e93994eddfc8651e696191964c31c65c56c6201`
- Verified commit `0e93994eddfc8651e696191964c31c65c56c6201` contains the nine UI
  files listed in `changed_files` plus the PKT-004 feedback bundle directory
- Re-checked the canonical Pantheon packet:
  - `docs/screens/PKT-004-deployment-approval-drilldowns.md`
  - `docs/bff/PKT-004-deployment-approval-drilldowns.md`
  - `docs/examples/PKT-004-deployment-approval-drilldowns.json`
  - `docs/pantheon-handoffs/PKT-004-deployment-approval-drilldowns/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/persona/types.ts`
  - `../front-ai-trading-system/src/pages/persona/DeploymentPlanList.tsx`
  - `../front-ai-trading-system/src/pages/persona/DeploymentPlanDetail.tsx`
  - `../front-ai-trading-system/src/pages/persona/ApprovalDecisionList.tsx`
  - `../front-ai-trading-system/src/pages/persona/ApprovalDecisionDetail.tsx`
  - `../front-ai-trading-system/src/pages/persona/PersonaDetail.tsx`
  - `../front-ai-trading-system/src/pages/persona/BindingDetail.tsx`

The reviewed UI remains aligned with the packet:

- `DeploymentPlanList` forwards `status` and `capital_pool_id` through the
  shared `personaDrilldownApi`
- `ApprovalDecisionList` forwards `outcome` and `state` through the shared
  `personaDrilldownApi`
- `DeploymentPlanDetail` links into `/deployment-review?plan=<plan_id>` and
  does not render approve, reject, or promote actions locally
- `ApprovalDecisionDetail` links into `/governance-review-queue` instead of
  inventing a review-queue join or command surface
- `PersonaDetail` and `BindingDetail` expose drilldown entry points without
  adding shadow state or client-side joins
- Missing required response fields still degrade into explicit contract-gap
  guidance instead of mocked rows

## Verified Pantheon Behavior

Pantheon reran the PKT-004 acceptance slice against the current BFF
implementation:

- `python3 -m pytest services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py services/control-plane/bff/test_w4_remaining_catalog.py -q`
  - Result: passed

Pantheon also ran a socket-level HTTP probe against a real local Uvicorn-served
FastAPI app bound to a seeded `ReadSurfaceStore` and verified:

- `GET /api/v1/deployment-plans?status=approved&capital_pool_id=pool-main` ->
  `200`, `meta.total = 1`
- `GET /api/v1/deployment-plans?status=rejected&capital_pool_id=pool-main` ->
  `200`, `meta.total = 0`
- `GET /api/v1/deployment-plans/plan-F-042` -> `200`
- `GET /api/v1/approval-decisions?outcome=approved&state=decided` -> `200`,
  `meta.total = 1`
- `GET /api/v1/approval-decisions?outcome=approved&state=pending` -> `200`,
  `meta.total = 0`
- `GET /api/v1/approval-decisions/approval-042` -> `200`
- viewer access to `GET /api/v1/deployment-plans` ->
  `403 INSUFFICIENT_ROLE`
- missing auth on `GET /api/v1/deployment-plans` -> `401 INVALID_TOKEN`

These checks confirm that the current Pantheon BFF honors the published PKT-004
list filters, detail routes, response envelope, and read-role boundary, and
that the local seeded acceptance path now returns the required `plan_id` field.

## Verification Performed

- Reviewed the current front request pair and feedback bundle listed above
- Verified the replay-clean implementation bundle at
  `0e93994eddfc8651e696191964c31c65c56c6201`
- Ran sibling front repo validation from the current checkout:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/persona/types.ts src/pages/persona/DeploymentPlanList.tsx src/pages/persona/DeploymentPlanDetail.tsx src/pages/persona/ApprovalDecisionList.tsx src/pages/persona/ApprovalDecisionDetail.tsx src/pages/persona/PersonaDetail.tsx src/pages/persona/BindingDetail.tsx`
  - Result: passed
- Landed Pantheon local acceptance fix:
  - `services/control-plane/bff/read_store.py`
  - `services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py`
  - Commit: `287a541774a3431522f815827c8dcf5ce7e71a4b`
- Ran targeted Pantheon regression tests:
  - `python3 -m pytest services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py services/control-plane/bff/test_w4_remaining_catalog.py -q`
  - Result: `2 passed`
- Ran a live local socket-level HTTP smoke against the seeded BFF process

## Non-Blocking Note

- The committed feedback bundle prose still references working-tree baseline
  `8d23e02dceb690ed35c1b3800749d2ca90ae4369`, but the authoritative
  machine-readable request pair correctly points at replay-clean source commit
  `0e93994eddfc8651e696191964c31c65c56c6201`

## Residual Risk

- No live browser QA against a deployed Pantheon environment was performed in
  this follow-up
- Honest mode without canonical or seeded data still returns empty or not-found
  responses by design; deployed acceptance still depends on real service-backed
  stores being present

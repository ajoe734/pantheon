# PKT-005 Global Degradation Banner — Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon reviewed the returned `frontend-feedback` and companion `ui-done`
handoffs for `PKT-005-degradation-banner` against the published PKT-005
contract, screen spec, example payloads, and the sibling
`front-ai-trading-system` checkout.

The reviewed UI cycle at front commit
`7406990a8311ef6865491fcdb883b677a98ff6c9` is acceptable for the intended
Operator Console scope:

- it builds successfully in the sibling repo
- targeted ESLint passes on the touched PKT-005 files
- the checked-in banner tests pass through `node:test`
- it uses only existing Pantheon BFF reads and a shared decision helper
- it does not add a health-check endpoint or client-side shadow state

Pantheon has now published the normalized PKT-005 packet family at commit
`77443032a240a3df49c329100ef2477a72a70e53`, so this Lovable loop no longer has
an open Pantheon follow-up leg.

## Front-End Review Outcome

- Pantheon review result: accepted and locked
- No Pantheon API gap is requested from the returned UI cycle
- No front-end rework is requested from this review
- Pantheon contract publication is complete under
  `pantheon-bff@77443032a240a3df49c329100ef2477a72a70e53`

## Verified UI Alignment

### Decision tree

- `src/lib/degradationBanner.ts` implements the intended banner decision tree:
  - `critical` when the request fails or all surfaces are unavailable
  - `partial` when any surface is unavailable
  - `stale` only when `served_from` is `cache` or `reconstructed` and at least
    one surface is degraded
  - `degraded` when surfaces are degraded without unavailability
  - `none` otherwise
- `src/components/GlobalDegradationBanner.test.tsx` passes for all five
  variants plus the PKT-002 split-read merge case.

### Operator-console route flow

- Deployment Review stays inside the intended route at `/deployment-review`,
  with row selection encoded in `?plan=<plan_id>` so the detail panel opens
  without navigating away from the screen.
- Incident Home and Incident Detail follow the expected operator-console flow:
  `/incidents` -> `/incidents/:incidentId`.
- Post-Incident Review stays inside `/post-incident-review`, with detail
  selection encoded in `?incident=<incident_id>` for the in-page composed
  review panel.

### Screen wiring

- `DeploymentReviewConsole.tsx` and `DeploymentPlanDetail.tsx` mount the shared
  banner using deployment list/detail `meta` only.
- `IncidentHome.tsx` merges only `GET /api/v1/incidents` and
  `GET /api/v1/kill-switch/status` `meta` fields through `mergeBannerMeta()`,
  pre-seeding `incident_list` and `kill_switch` as unavailable until they
  resolve.
- `IncidentDetail.tsx` derives banner state from
  `GET /api/v1/operator/incident-response/{incident_id}` `meta` only and keeps
  per-panel degraded or unavailable states explicit.
- `PostIncidentReviewConsole.tsx` derives banner state from
  `GET /api/v1/operator/post-incident-review/{incident_id}` `meta` only while
  preserving per-panel unavailable and degraded handling.

## Pantheon Contract Publication

- the sibling front repo has already published a real reviewed source commit:
  `7406990a8311ef6865491fcdb883b677a98ff6c9`
- the sibling front repo contains tracked canonical
  `.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml`
  and `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml`
  payloads at that source commit
- Pantheon now publishes the aligned PKT-005 contract family at
  `pantheon-bff@77443032a240a3df49c329100ef2477a72a70e53`
- the published packet uses `affected_bindings` and `allowedActions` for the
  incident-response surface keys, matching `PKT-002 Incident Detail`
- the published STALE rule now requires both cache or reconstructed delivery
  and at least one degraded surface, matching the reviewed banner helper

No additional UI replay is required for this feature.

## Verification Performed

- Reviewed the returned front-end feedback bundle in the sibling repo:
- `../front-ai-trading-system/.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml`
- `../front-ai-trading-system/.coordination/requests/PKT-005-degradation-banner-ui-done.yaml`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-degradation-banner/LOVABLE_CHANGE_FEEDBACK.md`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-degradation-banner/API_GAP_REQUESTS.json`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-degradation-banner/UI_DECISIONS.md`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-degradation-banner/QA_STATUS.md`
- Reviewed the sibling front repo files:
- `src/components/GlobalDegradationBanner.tsx`
- `src/components/GlobalDegradationBanner.test.tsx`
- `src/lib/degradationBanner.ts`
- `src/pages/operator/DeploymentReviewConsole.tsx`
- `src/pages/operator/DeploymentPlanDetail.tsx`
- `src/pages/operator/IncidentHome.tsx`
- `src/pages/operator/IncidentDetail.tsx`
- `src/pages/operator/PostIncidentReviewConsole.tsx`
- `src/App.tsx`
- `src/components/AppSidebar.tsx`
- Cross-checked against:
- `docs/pantheon-handoffs/PKT-005-degradation-banner/FRONTEND_CHANGE_SPEC.md`
- `docs/screens/PKT-005-degradation-banner.md`
- `docs/bff/PKT-005-degradation-banner.md`
- `docs/examples/PKT-005-degradation-banner.json`
- `docs/screens/PKT-001-deployment-review-console.md`
- `docs/screens/PKT-002-incident-home.md`
- `docs/screens/PKT-002-incident-detail.md`
- `docs/screens/PKT-003-post-incident-review-console.md`
- Ran sibling front repo validation:
- `npm run build`
- `npx eslint src/components/GlobalDegradationBanner.tsx src/components/GlobalDegradationBanner.test.tsx src/lib/degradationBanner.ts src/pages/operator/DeploymentReviewConsole.tsx src/pages/operator/DeploymentPlanDetail.tsx src/pages/operator/IncidentHome.tsx src/pages/operator/IncidentDetail.tsx src/pages/operator/PostIncidentReviewConsole.tsx`
- `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx`
- Result: passed

## Not Completed

- Live browser QA against a running Pantheon BFF was not performed in this
  review cycle.
- Pantheon has not yet published the normalized PKT-005 packet family under a
  live BFF deployment. This note records contract publication and review lock,
  not runtime rollout verification.

# PKT-013 Operator Home Dashboard Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-013-operator-home-ui-done.yaml` against the
current PKT-013 contract, example payload, coordination replay rules, the
sibling front implementation, and the local Pantheon BFF app.

The core PKT-013 read surface remains live in the current workspace:

- Pantheon serves `GET /api/v1/operator/home`
- targeted PKT-011 plus PKT-013 contract tests pass (`5 passed`)
- a direct local `TestClient` read returns `200 OK` with a contract-shaped
  degraded payload, backend-owned card and shortcut ordering, and
  browser-ready owner-link hrefs

No new endpoint or client-side shadow state is authorized in this cycle.

Pantheon's owner-link follow-up is now complete. The remaining next step is
front-owned publication replay only.

## Delivered Findings

### 1. Pantheon PKT-013 read route is live and contract-shaped in the current workspace

Published PKT-013 contract:

- `GET /api/v1/operator/home`

Observed runtime result in the current workspace:

- `200 OK` from local FastAPI `TestClient`
- `overall_status = degraded`
- `headline = 5 active operator alert(s)`
- `meta.surfaces.operator_home.status = degraded`
- card order = `alerts`, `incidents`, `governance`, `runtime`, `health`
- shortcut order = `open-alerts-rail`, `open-incident-home`,
  `open-health-status`, `open-approval-queue`, `open-runtime-state`

Observed automated verification:

- `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py services/control-plane/bff/test_pkt013_operator_home_contract.py -q`
- Result: `5 passed`

Impact:

- the reviewed screen can load its primary data path against the current
  Pantheon runtime
- degraded and unavailable PKT-013 behavior remains covered by targeted
  Pantheon contract tests in the current workspace

### 2. Pantheon now publishes browser-ready owner-link hrefs for PKT-013

Observed live Pantheon `href` values from `GET /api/v1/operator/home`:

- alerts -> `/alerts`
- incidents -> `/operator/incidents`
- governance -> `/governance-review-queue`,
  `/governance-approval-queue`
- runtime -> `/operator/runtime-state`
- health -> `/operator/health-status`
- shortcuts repeat those same browser-ready destinations in backend-owned order

Published lock changes in this workspace:

- `services/control-plane/bff/main.py`
- `docs/examples/PKT-011-health-status-board.json`
- `docs/examples/PKT-013-operator-home.json`
- `services/control-plane/bff/test_pkt011_health_status_board_contract.py`
- `services/control-plane/bff/test_pkt013_operator_home_contract.py`

Impact:

- the reviewed frontend can keep rendering payload refs verbatim and now lands
  on browser-ready owner destinations for the PKT-013 card targets and
  escalation shortcuts
- Pantheon's href-truth blocker is resolved for the current PKT-013 packet

### 3. The GitHub-visible front publication is still incomplete

Current front-repo state:

- `.coordination/requests/PKT-013-operator-home-frontend-feedback.yaml` is
  absent
- `.coordination/requests/PKT-013-operator-home-ui-done.yaml` exists only as a
  working-tree artifact and points `source_commit` at
  `37a622bca69a95e2aae46aa8c6b0432ad72082a8`
- commit `37a622bca69a95e2aae46aa8c6b0432ad72082a8` does not contain:
  - `src/pages/operator/OperatorHomeDashboard.tsx`
  - `.coordination/requests/PKT-013-operator-home-ui-done.yaml`
  - `docs/pantheon-feedback/PKT-013-operator-home/LOVABLE_CHANGE_FEEDBACK.md`

Impact:

- replay still cannot reconstruct the reviewed PKT-013 cycle from a truthful
  front commit tuple
- supervisor-visible closeout still cannot proceed until the front repo
  republishes the required request pair and feedback bundle from one immutable
  commit

### 4. The changed-file ESLint slice is not clean

Observed changed-file lint result:

- `npx eslint src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/lib/bffClient.ts src/pages/operator/OperatorHomeDashboard.tsx src/pages/operator/types.ts`
- Result: failed on `src/components/AppSidebar.tsx` due
  `@typescript-eslint/no-explicit-any`

Impact:

- this is front-owned typing cleanup rather than a PKT-013 contract blocker,
  but the reviewed change set is not lint-clean if changed-file ESLint is part
  of the next return

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Published endpoint: live in the current workspace
- Pantheon delivery completed:
  - kept `GET /api/v1/operator/home` live and contract-shaped
  - published browser-ready owner-link hrefs for PKT-013 card targets and
    escalation shortcuts
  - locked the updated href semantics in the canonical example payloads and
    targeted regression coverage
- Front follow-up still required:
  - publish the canonical `frontend-feedback` request and feedback bundle
  - republish `ui-done` from one truthful immutable front commit
  - fix the changed-file ESLint violation if lint cleanliness is required in
    the next return
- Current loop outcome: `delivered` on the Pantheon backend-delivery record;
  packet replay remains front-blocked until the canonical front publication
  tuple is truthful

## Verification Performed

- Reviewed the returned front-owned request artifact:
  - `../front-ai-trading-system/.coordination/requests/PKT-013-operator-home-ui-done.yaml`
- Reviewed the canonical packet:
  - `docs/bff/PKT-013-operator-home.md`
  - `docs/screens/PKT-013-operator-home.md`
  - `docs/examples/PKT-013-operator-home.json`
  - `docs/pantheon-handoffs/PKT-013-operator-home/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/OperatorHomeDashboard.tsx`
  - `src/pages/operator/types.ts`
- Ran sibling front verification:
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning
  - `npx eslint src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/lib/bffClient.ts src/pages/operator/OperatorHomeDashboard.tsx src/pages/operator/types.ts`
  - Result: failed on `src/components/AppSidebar.tsx` due
    `@typescript-eslint/no-explicit-any`
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py services/control-plane/bff/test_pkt013_operator_home_contract.py -q`
  - Result: `5 passed`
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient` and confirmed
  `GET /api/v1/operator/home` returns `200 OK` with browser-ready owner-link
  hrefs in the current workspace

## Not Completed

- No live browser QA against a deployed Pantheon environment was performed in
  this review cycle
- The front repo did not publish the required PKT-013 `frontend-feedback`
  request in this cycle

# PKT-011 Operator Health Status Board Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the returned PKT-011 `ui-done` and `frontend-feedback`
handoffs against the current contract, canonical example payload, sibling front
implementation, and the local Pantheon BFF workspace.

The Pantheon-owned PKT-011 route is live and aligned in the current workspace:

- `GET /api/v1/operator/health-status` returns `200 OK`
- targeted PKT-011 contract tests pass
- the live payload returns the canonical five-group order
- the live payload returns browser-ready `target_refs`
- the live payload returns the canonical advisory
  `secondary_control_path.targets[]` commands

Pantheon does not need a new PKT-011 endpoint, new route shape, or a payload
change in this cycle.

The loop stays open for two front-owned reasons:

1. the exact `source_commit` published in the returned request pair does not
   resolve in the sibling front repo
2. the governance `target_refs` still point to sibling front destinations that
   are not routed there yet

## Delivered Findings

### 1. Pantheon PKT-011 route and canonical payload are aligned

Published PKT-011 read surface:

- `GET /api/v1/operator/health-status`

Observed automated verification:

- `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py -q`
- Result: `3 passed`

Observed direct local runtime read:

- `200 OK` from local FastAPI `TestClient`
- `overall_status = degraded`
- `headline = Some services degraded`
- `secondary_control_path.mode = advisory`
- `groups = runtime, telemetry, incident, governance, kill_switch`
- `target_refs.runtime = /operator/runtime-state`
- `target_refs.incident = /operator/incidents`
- `target_refs.governance = /governance-review-queue`, `/governance-approval-queue`
- `target_refs.kill_switch = /operator/health-status`
- `secondary_control_path.targets[] = pantheon admin health`,
  `pantheon admin runtime status --runtime={runtime_id}`,
  `pantheon admin kill-switch status`

Impact:

- the reviewed screen can load its primary route against the current Pantheon
  runtime
- the current Pantheon contract and example payload stay truthful for PKT-011
- no Pantheon API gap was found in this pass

### 2. The returned UI remains aligned with the backend-owned single-route read model

Observed front behavior:

- `OperatorHealthStatusBoard.tsx` reads the page through
  `operatorApi.getHealthStatusBoard()`
- the screen renders overall status, safe-mode state, the five-group taxonomy,
  `surface_refs`, `target_refs`, and `secondary_control_path` from the PKT-011
  payload
- the screen does not assemble runtime, incident, governance, telemetry, or
  kill-switch state in the browser
- the screen renders `secondary_control_path.targets[]` verbatim
- the screen renders `target_refs[]` verbatim

Impact:

- the returned UI implementation remains consistent with the PKT-011 contract
- Pantheon does not need to request a new frontend data model for this screen

### 3. The exact published `source_commit` is malformed

Returned request-pair value:

- `87088d7a1efec434483fb97d16a3c34cbe9f37cd`

Observed verification:

- the exact advertised SHA does not resolve in the sibling front repo
- a similarly prefixed Git-visible commit,
  `87088d718dcbc6f07cc66932f44b5f16985583a9`, does exist and contains the
  reviewed PKT-011 request pair, feedback bundle, and UI wiring files
- Pantheon did not substitute that different commit for the exact published
  handoff value

Impact:

- the current PKT-011 request pair is not replay-clean under the coordination
  bus rules
- the next front cycle must correct the exact `source_commit` so the published
  handoff tuple is truthful and replayable

### 4. Governance owner-link destinations remain unresolved in the sibling front app

Observed live payload values:

- `Governance Review Queue -> /governance-review-queue`
- `Governance Approval Queue -> /governance-approval-queue`

Observed sibling front router state:

- routed:
  - `/operator/health-status`
  - `/operator/runtime-state`
  - `/operator/incidents`
- not routed:
  - `/governance-review-queue`
  - `/governance-approval-queue`

Impact:

- the backend-owned `secondary_control_path.targets[]` resolve as intended
- runtime, telemetry, incident, and health-status owner links align
- governance owner-link resolution is still incomplete in the sibling front app

## Pantheon-Side Outcome

- Pantheon contract: unchanged in this review cycle
- Pantheon endpoint: already live in the current workspace
- Pantheon response published:
  - refreshed the mirrored PKT-011 `ui-done` request in Pantheon
  - mirrored the returned PKT-011 `frontend-feedback` request into Pantheon
  - refreshed the PKT-011 review packet
  - refreshed the PKT-011 backend-delivery response
  - refreshed the PKT-011 delivery note and contract lock
- Front follow-up still required:
  - correct the exact published `source_commit` in the returned request pair
  - expose `/governance-review-queue` and `/governance-approval-queue`, or
    emit the next coordination handoff explicitly naming that dependency
- Pantheon follow-up required: none in this cycle
- Current loop outcome: `followup-required`

## Verification Performed

- Reviewed the returned front-owned request artifacts:
  - `../front-ai-trading-system/.coordination/requests/PKT-011-health-status-board-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-011-health-status-board-frontend-feedback.yaml`
- Reviewed the returned front-owned feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/QA_STATUS.md`
- Reviewed the canonical packet:
  - `docs/bff/PKT-011-health-status-board.md`
  - `docs/screens/PKT-011-health-status-board.md`
  - `docs/examples/PKT-011-health-status-board.json`
  - `docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/operator/OperatorHealthStatusBoard.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py -q`
  - Result: `3 passed`
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient` and confirmed
  `GET /api/v1/operator/health-status` returns `200 OK` with contract-shaped
  PKT-011 payload fields in the current workspace

## Not Completed

- No live browser QA against a deployed Pantheon environment was performed in
  this review cycle
- Pantheon did not rerun the sibling front production build or ESLint slice in
  this review cycle

# PKT-011 Health Status Board Review Packet

## Date

2026-04-19

## Reviewer

Codex

## Findings

### 1. High: the exact published `source_commit` is invalid, so the PKT-011 handoff is not replay-clean

- The returned front request pair exists in the sibling checkout:
  - `../front-ai-trading-system/.coordination/requests/PKT-011-health-status-board-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-011-health-status-board-frontend-feedback.yaml`
- The returned feedback bundle also exists in the sibling checkout:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/`
- Both request payloads advertise the same `source_commit`:
  `87088d7a1efec434483fb97d16a3c34cbe9f37cd`.
- The exact advertised SHA does not resolve in the sibling front repo.
- There is a similarly prefixed Git-visible front commit,
  `87088d718dcbc6f07cc66932f44b5f16985583a9`, that contains the reviewed
  PKT-011 request pair, feedback bundle, and UI wiring files, but Pantheon
  cannot silently substitute that different SHA for the exact value published
  in the returned handoff.
- The coordination loop requires the published `payload_path + source_commit`
  tuple to be truthful and replayable from Git-visible history.

Impact:

- Pantheon can review the current sibling workspace and confirm the UI is wired
  to the right route, but it cannot mark the PKT-011 return as replay-clean
  while the exact published `source_commit` is malformed.
- The next front cycle must correct the request pair so both payloads point to
  one exact Git-visible commit that resolves and contains the reviewed PKT-011
  bundle.

### 2. Medium: governance `target_refs` still do not resolve in the sibling front router

- The live Pantheon PKT-011 route returns browser-oriented `target_refs` that
  match the canonical example payload:
  - runtime -> `/operator/runtime-state`
  - telemetry -> `/operator/runtime-state`
  - incident -> `/operator/incidents`
  - governance -> `/governance-review-queue`,
    `/governance-approval-queue`
  - kill_switch -> `/operator/health-status`
- The same live payload returns the canonical advisory secondary-control-path
  commands:
  - `pantheon admin health`
  - `pantheon admin runtime status --runtime={runtime_id}`
  - `pantheon admin kill-switch status`
- The sibling front router currently exposes:
  - `/operator/health-status`
  - `/operator/runtime-state`
  - `/operator/incidents`
- The sibling front router does not expose:
  - `/governance-review-queue`
  - `/governance-approval-queue`
- The reviewed screen renders backend-owned `target_refs[]` and
  `secondary_control_path.targets[]` verbatim, which is the correct PKT-011 UI
  behavior.

Impact:

- The backend-owned secondary control path is valid and resolves as intended.
- Runtime, telemetry, incident, and health-status owner links are aligned.
- Governance owner links still land on destinations that are not routed in the
  sibling front app, so operator-owned navigation remains only partially
  resolved for this cycle.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-011-health-status-board.md`
  - `docs/examples/PKT-011-health-status-board.json`
  - `docs/screens/PKT-011-health-status-board.md`
  - `docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md`
- Returned front-owned requests:
  - `../front-ai-trading-system/.coordination/requests/PKT-011-health-status-board-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-011-health-status-board-frontend-feedback.yaml`
- Returned front-owned feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-011-health-status-board/QA_STATUS.md`
- Reviewed front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/operator/OperatorHealthStatusBoard.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
- Pantheon BFF verification:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt011_health_status_board_contract.py`
  - `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`

## Verified Positives

- The reviewed screen stays on the single PKT-011 read surface and uses
  `operatorApi.getHealthStatusBoard()`; no component-level raw network call
  was introduced.
- The reviewed UI renders backend-owned overall status, safe-mode state,
  five-group taxonomy, `surface_refs`, `target_refs`, and
  `secondary_control_path` without reconstructing runtime, incident,
  governance, or kill-switch data in the browser.
- `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py -q`
  passed in the current Pantheon workspace (`3 passed`).
- A direct local FastAPI `TestClient` read of
  `GET /api/v1/operator/health-status` returned `200 OK` with:
  - `overall_status = degraded`
  - canonical group order:
    `runtime`, `telemetry`, `incident`, `governance`, `kill_switch`
  - browser-ready `target_refs`
  - advisory `secondary_control_path.mode`
  - canonical secondary-control-path commands:
    `pantheon admin health`,
    `pantheon admin runtime status --runtime={runtime_id}`,
    `pantheon admin kill-switch status`
- The backend-owned degraded fallback targets align with
  `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`.

## Not Completed

- I did not rerun the sibling front production build or ESLint slice in this
  pass.
- I did not run live browser QA against a deployed Pantheon environment in
  this pass.

## Decision

`PKT-011-health-status-board` is **follow-up required**.

Pantheon's current PKT-011 route, contract, example payload, and
secondary-control-path guidance are aligned. The remaining blockers are now
front-owned:

- correct the exact published `source_commit` so the returned request pair is
  replay-clean
- resolve or explicitly hand off the missing governance route destinations in
  the sibling front app

## Required Follow-up

1. Front repo: correct the PKT-011 request pair so both payloads point
   `source_commit` at one exact Git-visible commit that resolves and contains
   the reviewed PKT-011 request pair, feedback bundle, and UI files.
2. Front repo: keep rendering `target_refs[]` and
   `secondary_control_path.targets[]` verbatim; do not synthesize alternate
   browser destinations or fallback commands locally.
3. Front repo: either expose `/governance-review-queue` and
   `/governance-approval-queue` in the sibling router, or emit the next
   coordination handoff explicitly naming governance-route dependency as the
   remaining downstream blocker for PKT-011 owner-link resolution.

## 2026-04-19 Closeout Addendum

The remaining front-owned blockers are now resolved.

- The PKT-011 request pair is replay-clean from
  `be42f22c2388076af4bb7b1f1d4209aaf90af6a8`.
- The sibling router now exposes `/governance-review-queue` and
  `/governance-approval-queue`, so the backend-owned `target_refs[]` and
  `secondary_control_path.targets[]` resolve without shadow routing.
- Pantheon's PKT-011 contract remains green:
  `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py -q`
  -> `3 passed`.

## Final Decision

**APPROVED.**

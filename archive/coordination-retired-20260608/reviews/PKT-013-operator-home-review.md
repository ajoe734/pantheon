# PKT-013 Operator Home Dashboard Review Packet

## Date

2026-04-18

## Reviewer

Codex

## Delivery Addendum

Re-checked on 2026-04-18 after the Pantheon href-truth follow-up landed.

- `GET /api/v1/operator/home` now returns browser-ready owner-link hrefs for
  all PKT-013 card targets and escalation shortcuts:
  - alerts -> `/alerts`
  - incidents -> `/operator/incidents`
  - governance -> `/governance-review-queue`,
    `/governance-approval-queue`
  - runtime -> `/operator/runtime-state`
  - health -> `/operator/health-status`
- The inherited PKT-011 group refs used by the PKT-013 governance and incident
  cards were updated in the current workspace to the same browser-route
  semantics.
- `python3 -m pytest services/control-plane/bff/test_pkt011_health_status_board_contract.py services/control-plane/bff/test_pkt013_operator_home_contract.py -q`
  passes in the current workspace (`5 passed`).

Pantheon-side disposition is now reduced to front publication replay only. The
remaining blockers are the absent canonical `frontend-feedback` request, the
non-truthful front `source_commit`, and the deferred `AppSidebar.tsx`
changed-file lint cleanup.

## 2026-04-19 Follow-up Addendum

- The sibling front workspace now includes the canonical
  `PKT-013-operator-home-frontend-feedback.yaml` request and the full
  `docs/pantheon-feedback/PKT-013-operator-home/` bundle.
- The previously cited `AppSidebar.tsx` changed-file ESLint issue is now clean
  in the local sibling workspace.
- The remaining blocker is therefore reduced to truthful Git-visible
  publication replay: the reviewed request pair, feedback bundle, and UI files
  still need to land together in one immutable front commit, and both request
  payloads must point at that exact commit SHA.

## Findings

### 1. High: the returned PKT-013 handoff is not publishable under the coordination loop rules

- The dispatched `ui-done` payload points `source_commit` at
  `37a622bca69a95e2aae46aa8c6b0432ad72082a8` in
  `../front-ai-trading-system/.coordination/requests/PKT-013-operator-home-ui-done.yaml:5`.
- The required paired `frontend-feedback` request is absent:
  `../front-ai-trading-system/.coordination/requests/PKT-013-operator-home-frontend-feedback.yaml`
  does not exist.
- Commit `37a622bca69a95e2aae46aa8c6b0432ad72082a8` does not contain:
  - `src/pages/operator/OperatorHomeDashboard.tsx`
  - `.coordination/requests/PKT-013-operator-home-ui-done.yaml`
  - `docs/pantheon-feedback/PKT-013-operator-home/LOVABLE_CHANGE_FEEDBACK.md`
- The sibling front checkout still holds the PKT-013 request and feedback
  bundle only in the working tree, so replay cannot reconstruct the reviewed
  publication set from the advertised commit tuple.

Impact:

- Pantheon can review the local sibling checkout, but it cannot honestly mark
  the PKT-013 handoff replay-clean or supervisor-complete through GitHub-visible
  coordination artifacts until the request pair and feedback bundle are
  published together from one truthful immutable front commit.

### 2. Medium: Pantheon still publishes API-resource hrefs where PKT-013 needs truthful owner-link semantics

- The PKT-013 contract and screen packet say the dashboard links only through
  backend-supplied `target_refs[]` and renders `Existing-owner links` from
  those payload refs:
  `docs/bff/PKT-013-operator-home.md:55-60`,
  `docs/screens/PKT-013-operator-home.md:16-20`.
- The reviewed frontend correctly renders card `target_refs[]` and
  `escalation_shortcuts[]` hrefs verbatim in
  `../front-ai-trading-system/src/pages/operator/OperatorHomeDashboard.tsx:353-356`
  and
  `../front-ai-trading-system/src/pages/operator/OperatorHomeDashboard.tsx:400-403`.
- A direct local FastAPI `TestClient` read of `GET /api/v1/operator/home`
  returned API-resource hrefs instead:
  - alerts -> `/api/v1/operator/alerts`
  - incidents -> `/api/v1/incidents`
  - governance -> `/api/v1/operator/governance/review-queue`,
    `/api/v1/operator/governance/approval-queue`
  - runtime -> `/api/v1/operator/runtime-state`
  - health -> `/api/v1/operator/health-status`
  - shortcuts repeat those same API-resource targets
- The sibling app routes expose browser pages at
  `../front-ai-trading-system/src/App.tsx:120-128`, including
  `/operator/home`, `/operator/incidents`, `/operator/health-status`, and
  `/operator/runtime-state`, while governance is still only a `/governance`
  placeholder and no browser route matches the emitted `/api/v1/...` links.

Impact:

- The reviewed UI behaves correctly by staying payload-owned, but the current
  Pantheon href values are not yet truthfully aligned with the deployed
  operator screen surface.
- Pantheon must either publish browser-ready owner links or revise the PKT-013
  packet and example payload so they explicitly describe API-resource links
  instead of screen destinations.

### 3. Low: the changed-file ESLint slice is not clean

- Targeted ESLint against the reviewed PKT-013 files fails on:
  - `../front-ai-trading-system/src/components/AppSidebar.tsx:37`
  - error: `Unexpected any. Specify a different type`

Impact:

- This is front-owned typing cleanup rather than a PKT-013 contract blocker,
  but the reviewed change set is not lint-clean if changed-file ESLint is part
  of the next return.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-013-operator-home.md`
  - `docs/examples/PKT-013-operator-home.json`
  - `docs/screens/PKT-013-operator-home.md`
  - `docs/pantheon-handoffs/PKT-013-operator-home/FRONTEND_CHANGE_SPEC.md`
- Coordination sources:
  - `../front-ai-trading-system/.coordination/requests/PKT-013-operator-home-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/responses/PKT-013-operator-home-contract-ready.yaml`
  - `../front-ai-trading-system/.coordination/responses/PKT-013-operator-home-lovable-ui-task.yaml`
- Front feedback bundle present in the sibling working tree:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-013-operator-home/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-013-operator-home/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-013-operator-home/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-013-operator-home/QA_STATUS.md`
- Reviewed front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/operator/OperatorHomeDashboard.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
- Pantheon BFF implementation and validation:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt013_operator_home_contract.py`

## Verified Positives

- The screen is wired through the shared `operatorApi.getOperatorHome()`
  helper in `../front-ai-trading-system/src/lib/bffClient.ts`; no
  component-level raw `fetch` path was introduced.
- The reviewed UI validates required top-level fields, `safe_mode_state`, the
  five-card set, and all required `meta.surfaces.*` keys before rendering, and
  it instructs the operator to emit a `bff-gap` handoff instead of rebuilding
  dashboard state locally.
- The reviewed UI preserves backend-owned card and shortcut ordering and keeps
  safe-mode state rendered directly from the PKT-013 payload.
- `overall_status = unavailable` keeps the card stack visible and renders the
  explicit unavailable warning instead of a calm empty dashboard.
- The sibling front production build passed:
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning
- Targeted Pantheon verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt013_operator_home_contract.py -q`
  - Result: `2 passed`
- Direct local BFF probing with FastAPI `TestClient` returned `200 OK` for
  `GET /api/v1/operator/home` in the current workspace with truthful degraded
  behavior:
  - `overall_status = degraded`
  - `headline = 5 active operator alert(s)`
  - `meta.surfaces.operator_home.status = degraded`
  - card order = `alerts`, `incidents`, `governance`, `runtime`, `health`
  - shortcut order = `open-alerts-rail`, `open-incident-home`,
    `open-health-status`, `open-approval-queue`, `open-runtime-state`
- The unavailable branch is covered by the targeted PKT-013 contract test
  slice even though the current workspace probe surfaced the degraded branch.

## Decision

`PKT-013-operator-home` is **approved for closeout**.

The primary read route is live in the current Pantheon workspace, the reviewed
UI remains aligned on the single-route operator-home contract, and degraded plus
unavailable behavior is covered through targeted Pantheon verification.

- Front transport commit `be42f22c2388076af4bb7b1f1d4209aaf90af6a8` now
  contains the PKT-013 request pair, feedback bundle, reviewed Operator Home
  files, and the browser-routed governance owner-link aliases.
- Canonical request-pair commit
  `2779d23c3a6b0fb999eaf25df8402ea72601293c` now points both PKT-013 request
  bodies at that exact transport commit on the pushed `pkt-004-detail-fix`
  branch.
- The local changed-file ESLint slice is now clean in the sibling front
  workspace.

Loop can close from the Pantheon side.

## Residual Risk

- No live browser QA was rerun in this closeout step.
- Governance owner links currently land on the shared Governance Queue
  placeholder in the sibling app. That is acceptable for this closeout because
  the reviewed UI renders backend-owned hrefs verbatim and those hrefs now land
  on routed destinations.

# PKT-012 Operator Alerts Rail Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-012-alerts-rail-ui-done.yaml` against the current
PKT-012 contract, example payload, coordination replay rules, the sibling
front implementation, and the local Pantheon BFF app.

The core PKT-012 read surface is live in the current workspace:

- Pantheon serves `GET /api/v1/operator/alerts`
- targeted PKT-012 contract tests pass
- a direct local `TestClient` read returns `200 OK` with a contract-shaped
  degraded payload and browser-style target refs
- the reviewed front changed-file build and eslint slice pass

No new endpoint or client-side shadow state is authorized in this cycle.

Pantheon's href-truth follow-up is now complete in this workspace. The loop
still cannot close because the current PKT-012 handoff is not yet Git-visible
or replay-clean. The sibling front checkout now contains the frontend-feedback
request plus the required feedback bundle, but commit
`37a622bca69a95e2aae46aa8c6b0432ad72082a8` does not contain those files or the
mirrored `ui-done` request yet.

## Verified UI Alignment

- `OperatorAlertsRail.tsx` reads the screen through
  `operatorApi.getAlertsRail()` and does not add raw component-level network
  calls.
- The rail validates the required PKT-012 fields and surface keys before
  accepting the payload.
- The page renders alert identity, severity, category, summary counts, and
  `target_ref` values directly from the backend-owned response.
- The reviewed screen shows the shared degradation banner from `meta.surfaces`
  and does not reconstruct alerts from incident, governance, kill-switch,
  runtime, or telemetry primitives in the browser.
- The reviewed front changed-file slice passes both production build and the
  targeted eslint command.

## Delivered Findings

### 1. Pantheon PKT-012 read route is live and contract-shaped

Published PKT-012 contract:

- `GET /api/v1/operator/alerts`

Observed runtime result in the current workspace:

- `200 OK` from local FastAPI `TestClient`
- `summary.total_active = 5`
- `summary.highest_severity = critical`
- `meta.surfaces.alerts.status = degraded`
- browser-style `target_ref.href` values, including:
  - `/governance-approval-queue`
  - `/operator/runtime-state`
  - `/operator/incidents/inc-20260410-001`

Observed automated verification:

- `python3 -m pytest services/control-plane/bff/test_pkt012_alerts_rail_contract.py -q`
- Result: `2 passed`

Impact:

- the reviewed screen can load its primary data path against the current
  Pantheon runtime
- degraded and unavailable PKT-012 behavior is covered by targeted Pantheon
  contract tests in the current workspace
- the local Pantheon contract, example payload, and regression slice now agree
  on browser-style owner-link semantics for `target_ref.href`

### 2. The reviewed front changed-file slice is buildable and lint-clean

Observed sibling front verification:

- `npm run build`
- Result: passed with the existing non-blocking Vite chunk-size warning
- `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/operator/OperatorAlertsRail.tsx src/pages/operator/types.ts`
- Result: passed

Impact:

- the current Pantheon block is not caused by a local front build failure or a
  changed-file eslint regression in the reviewed PKT-012 slice
- remaining blockers stay on publication replay truth, not on a local PKT-012
  contract or route gap

### 3. The GitHub-visible front publication is still incomplete

Current front-repo state:

- `.coordination/requests/PKT-012-alerts-rail-ui-done.yaml` exists in the
  working tree and points `source_commit` at
  `37a622bca69a95e2aae46aa8c6b0432ad72082a8`
- `.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml` also
  exists in the working tree and points `source_commit` at that same SHA
- `docs/pantheon-feedback/PKT-012-alerts-rail/` exists in the working tree
- `git show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:<path>` fails for both
  request files and all four feedback-bundle files, so the advertised commit
  does not contain the reviewed handoff set

Impact:

- replay cannot reconstruct the reviewed PKT-012 cycle from a truthful front
  commit tuple
- supervisor-visible closeout cannot proceed until the front repo republishes
  the required request pair and feedback bundle from one immutable commit

### 4. Deployed-environment owner-link confirmation remains deferred runtime QA

Current local Pantheon state:

- the PKT-012 contract, example payload, and targeted regression slice all use
  browser-style owner-link hrefs
- the reviewed frontend renders those hrefs verbatim without remapping

Not yet completed in this cycle:

- no live browser session against a deployed Pantheon environment
- no deployed-environment confirmation that each returned `target_ref.href`
  lands on the intended owner screen outside the local contract/test workspace

Impact:

- no new local Pantheon contract blocker is open for `target_ref.href`
- live deployed-environment confirmation remains deferred non-blocking QA

## Pantheon-Side Outcome

- Pantheon contract: unchanged in this review cycle
- Published endpoint: already live in the current workspace
- Pantheon delivery recorded:
  - mirrored the returned `ui-done` request into Pantheon coordination state
  - mirrored the returned `frontend-feedback` request into Pantheon
    coordination state
  - published the reviewed PKT-012 frontend-feedback summary
  - recorded the PKT-012 review packet and delivery lock
  - published the backend-delivery response for the next front-owned cycle
- Front follow-up still required:
  - publish the canonical request pair and feedback bundle from one truthful
    immutable front commit
  - repoint `source_commit` in both request bodies at that exact publication
    commit
- Pantheon follow-up still optional runtime QA:
  - confirm in a deployed environment that the published `target_ref.href`
    values land on the intended owner screens
- Current loop outcome: `delivered` on the Pantheon backend-delivery record;
  packet loop remains open until the front publication tuple is truthful and
  replay-clean

## Verification Performed

- Reviewed the returned front-owned request pair:
  - `../front-ai-trading-system/.coordination/requests/PKT-012-alerts-rail-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml`
- Reviewed the canonical packet:
  - `docs/bff/PKT-012-alerts-rail.md`
  - `docs/screens/PKT-012-alerts-rail.md`
  - `docs/examples/PKT-012-alerts-rail.json`
  - `docs/pantheon-handoffs/PKT-012-alerts-rail/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/OperatorAlertsRail.tsx`
  - `src/pages/operator/types.ts`
- Reviewed the front feedback bundle:
  - `docs/pantheon-feedback/PKT-012-alerts-rail/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-012-alerts-rail/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-012-alerts-rail/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-012-alerts-rail/QA_STATUS.md`
- Ran sibling front verification:
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/operator/OperatorAlertsRail.tsx src/pages/operator/types.ts`
  - Result: passed
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt012_alerts_rail_contract.py -q`
  - Result: `2 passed`
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient` and confirmed
  `GET /api/v1/operator/alerts` returns `200 OK` with contract-shaped PKT-012
  payload fields plus browser-style `target_ref.href` values in the current
  workspace
- Verified transport replay state against the advertised front commit:
  - `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-012-alerts-rail-ui-done.yaml`
  - `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml`
  - `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:docs/pantheon-feedback/PKT-012-alerts-rail/...`
  - Result: all required PKT-012 coordination artifacts are absent from that
    commit

## Not Completed

- No live browser QA against a deployed Pantheon environment was performed in
  this review cycle
- The front repo did not publish the current PKT-012 request pair and feedback
  bundle from one immutable Git-visible commit in this cycle

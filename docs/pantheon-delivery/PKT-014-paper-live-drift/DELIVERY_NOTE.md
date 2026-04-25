# PKT-014 Operator Paper / Live Drift Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the returned `ui-done` and `frontend-feedback` handoffs:

- `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
- `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`

against the current PKT-014 contract, example payload, coordination replay
rules, the sibling front implementation, and the local Pantheon BFF app.

The core PKT-014 read surface is live in the current workspace:

- Pantheon serves `GET /api/v1/operator/paper-live-drift/{runtime_id}`
- targeted PKT-014 contract tests pass
- an authenticated local `TestClient` read returns `200 OK` with a
  contract-shaped paper/live drift payload for `runtime-042`
- the current workspace returns browser-ready owner-link refs for `plan_ref`,
  `evidence_refs[]`, and `recommended_actions[]`
- degraded and unavailable branches are covered by the targeted PKT-014
  contract test slice in the current workspace

No new endpoint or client-side shadow state is authorized in this cycle.

The loop still cannot close for one concrete reason:

1. the front-owned request pair and feedback bundle are present only in the
   sibling working tree, while the advertised `source_commit`
   `37a622bca69a95e2aae46aa8c6b0432ad72082a8` does not contain the reviewed
   screen file, request pair, or feedback bundle in Git history

## Verified UI Alignment

- `OperatorPaperLiveDrift.tsx` reads the screen through
  `operatorApi.getPaperLiveDrift(runtimeId)` and does not add raw
  component-level network calls.
- The page validates required top-level fields and the full required
  `meta.surfaces.*` map before rendering.
- The reviewed UI preserves backend-owned `drift_groups[]` order,
  `metrics[]` order, `threshold_evaluation`, `evidence_refs[]`, and
  `recommended_actions[]` directly from the PKT-014 payload.
- `meta.surfaces.paper_live_drift = unavailable` renders the explicit
  unavailable treatment and suppresses comparison math instead of rebuilding
  drift state from adjacent approval, incident, telemetry, or evolution data.
- The reviewed screen renders `plan_ref.href`, `evidence_refs[].href`, and
  `recommended_actions[].target_ref.href` verbatim and does not synthesize
  alternate browser routes.

## Delivered Findings

### 1. Pantheon PKT-014 read route is live, contract-shaped, and now returns owner-link hrefs

Published PKT-014 contract:

- `GET /api/v1/operator/paper-live-drift/{runtime_id}`

Observed runtime result in the current workspace:

- `200 OK` from authenticated local FastAPI `TestClient`
- `meta.surfaces.paper_live_drift.status = ok`
- `meta.surfaces.drift_report.status = ok`
- `threshold_evaluation.overall_status = breached`
- `plan_ref.href = /operator/deployment-plans/plan-F-042`
- evidence refs ->
  `/governance-approval-queue`,
  `/operator/incidents/inc-20260410-001`,
  `/operator/post-incident-review?incident=inc-20260410-001`
- recommended actions ->
  `/operator/deployment-review?plan=plan-F-042`,
  `/operator/incidents/inc-20260410-001`,
  `/operator/post-incident-review?incident=inc-20260410-001`

Observed automated verification:

- `python3 -m pytest services/control-plane/bff/test_pkt014_paper_live_drift_contract.py -q`
- Result: `2 passed`

Impact:

- the reviewed screen can load its primary data path against the current
  Pantheon runtime
- Pantheon-owned href truth is resolved in the current workspace
- degraded and unavailable PKT-014 behavior remains covered by targeted
  Pantheon verification

### 2. The GitHub-visible front publication is still incomplete

Current front-repo state:

- the sibling working tree contains both canonical requests and the full
  feedback bundle
- both requests advertise:
  - `source_branch: pkt-004-detail-fix`
  - `source_commit: 37a622bca69a95e2aae46aa8c6b0432ad72082a8`
- `git ls-tree -r 37a622bca69a95e2aae46aa8c6b0432ad72082a8` from the sibling repo
  contains only:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/types.ts`
- that advertised commit does not contain:
  - `src/pages/operator/OperatorPaperLiveDrift.tsx`
  - `.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
  - `.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-014-paper-live-drift/*`

Impact:

- replay cannot reconstruct the reviewed PKT-014 cycle from the advertised
  front commit tuple
- supervisor-visible closeout cannot proceed until the front repo republishes
  the required request pair, feedback bundle, and reviewed screen from one
  immutable commit

## Pantheon-Side Outcome

- Pantheon contract: unchanged in this review cycle
- Published endpoint: already live in the current workspace
- Pantheon runtime follow-up: resolved
- Pantheon delivery recorded:
  - mirrored the returned front-owned request pair into Pantheon coordination
    state
  - published the reviewed PKT-014 frontend-feedback summary
  - recorded the PKT-014 review packet and delivery lock
  - published the backend-delivery response for the next front-owned cycle
- Front follow-up still required:
  - republish the canonical request pair and feedback bundle from one truthful
    immutable front commit that also contains `OperatorPaperLiveDrift.tsx`
- Current loop outcome: `followup-required` on the Pantheon backend-delivery
  record; the packet remains open only for front publication replay

## Verification Performed

- Reviewed the returned front-owned request artifacts:
  - `../front-ai-trading-system/.coordination/requests/PKT-014-paper-live-drift-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-014-paper-live-drift-frontend-feedback.yaml`
- Reviewed the canonical packet:
  - `docs/bff/PKT-014-paper-live-drift.md`
  - `docs/examples/PKT-014-paper-live-drift.json`
  - `docs/screens/PKT-014-paper-live-drift.md`
  - `docs/pantheon-handoffs/PKT-014-paper-live-drift/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/OperatorPaperLiveDrift.tsx`
  - `src/pages/operator/types.ts`
- Verified the advertised front commit contents:
  - `git -C ../front-ai-trading-system ls-tree -r 37a622bca69a95e2aae46aa8c6b0432ad72082a8 -- ...`
- Ran sibling front verification:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - Result: passed
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/operator/OperatorPaperLiveDrift.tsx src/pages/operator/types.ts`
  - Result: passed
  - `npm run build`
  - Result: passed with the existing non-blocking Vite chunk-size warning
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt014_paper_live_drift_contract.py -q`
  - Result: `2 passed`
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient` and confirmed
  authenticated `GET /api/v1/operator/paper-live-drift/runtime-042` returns
  `200 OK` with browser-ready owner-link refs in the current workspace

## Not Completed

- No live browser QA against a deployed Pantheon environment was performed in
  this review cycle
- No deployed-environment confirmation was captured for whether the same
  owner-link href semantics observed locally are already live in the deployed
  environment

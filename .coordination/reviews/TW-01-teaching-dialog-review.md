# TW-01 Teaching Dialog Review Packet

## Date

2026-04-20

## Reviewer

Codex

## Findings

### 1. High: live HTTP acceptance is still blocked because the active operator-bff runtime does not yet serve the advertised TW-01 route family

- The current Pantheon workspace still implements and verifies the TW-01 route
  family locally:
  - `python3 -m pytest services/control-plane/bff/test_tw01_teaching_dialog_contract.py -q`
  - Result: `5 passed`
- The active runtime on `http://127.0.0.1:18001` is stale relative to that
  workspace:
  - `GET /api/v1/trainer/sessions?persona_id=persona-alpha` returned
    `404 Not Found`
  - `GET /openapi.json` on port `18001` exposes no `/api/v1/trainer/*` paths
- Impact: the requested live-BFF and deployed-environment verification cannot
  close against the running deployment target yet. Pantheon still needs the
  runtime follow-up in `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`.

### 2. High: the advertised TW-01 source commit is still not replayable from GitHub-visible history

- The sibling front repo now has a real local TW-01 publication commit:
  - `4d19e0f31104e87294e267e1e6e1bc36065bf961`
- `git -C ../front-ai-trading-system ls-tree -r --name-only 4d19e0f31104e87294e267e1e6e1bc36065bf961 -- .coordination/requests/TW-01-teaching-dialog-ui-done.yaml .coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml docs/pantheon-feedback/TW-01-teaching-dialog src/pages/trainer/TeachingDialogList.tsx src/pages/trainer/TeachingDialogDetail.tsx src/pages/trainer/types.ts src/lib/bffClient.ts src/App.tsx`
  confirms that commit contains the canonical request pair, feedback bundle,
  route wiring, and TW-01 UI files together.
- Pantheon fetched remotes and checked remote containment:
  - `git -C ../front-ai-trading-system fetch origin --prune`
  - `git -C ../front-ai-trading-system branch -r --contains 4d19e0f31104e87294e267e1e6e1bc36065bf961`
  - Result: no remote branch contains that SHA
- Impact: the TW-01 return is internally consistent locally, but it is still
  not GitHub-visible transport truth. Supervisor replay and GitHub-based audit
  cannot reconstruct this cycle from a published remote commit yet.

## Reviewed Artifacts

- Pantheon contract bundle:
  - `docs/bff/TW-01-teaching-dialog.md`
  - `docs/examples/TW-01-teaching-dialog.json`
  - `docs/screens/TW-01-teaching-dialog.md`
  - `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/TW-01-teaching-dialog-contract-ready.yaml`
  - `.coordination/responses/TW-01-teaching-dialog-lovable-ui-task.yaml`
- Pantheon coordination state:
  - `.coordination/requests/TW-01-teaching-dialog-ui-done.yaml`
  - `.coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml`
  - `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`
- Returned front-owned artifacts at
  `4d19e0f31104e87294e267e1e6e1bc36065bf961`:
  - `../front-ai-trading-system/.coordination/requests/TW-01-teaching-dialog-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-01-teaching-dialog/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-01-teaching-dialog/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-01-teaching-dialog/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/TW-01-teaching-dialog/QA_STATUS.md`
  - `../front-ai-trading-system/src/pages/trainer/TeachingDialogList.tsx`
  - `../front-ai-trading-system/src/pages/trainer/TeachingDialogDetail.tsx`
  - `../front-ai-trading-system/src/pages/trainer/types.ts`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/App.tsx`
- Pantheon BFF verification:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/read_store.py`
  - `services/control-plane/bff/test_tw01_teaching_dialog_contract.py`

## Verified Positives

- The local TW-01 publication commit `4d19e0f31104e87294e267e1e6e1bc36065bf961`
  removes the prior pending-BFF gate and keeps the trainer list and detail
  screens on the live TW-01 route family only.
- The session composer still preserves optional `context_refs[]` aligned to the
  published create contract.
- The detail screen continues to render transcript rows from backend-owned
  `events[]`, gates writes from `allowedActions.canSendMessage`, and refreshes
  detail state from the backend after message send instead of mutating local
  transcript state.
- The current front router mounts the exact owner-screen route that Pantheon
  returns in local TW-01 payloads:
  - mounted route: `/trainer/sessions/:session_id`
  - observed local `links.workbench_detail`: `/trainer/sessions/{session_id}`
- Local FastAPI `TestClient` verification confirms the published TW-01 surface
  semantics:
  - degraded list: `200`, `meta.surfaces.trainer_dialog = degraded`,
    `links.workbench_detail = /trainer/sessions/trn-20260419-001`
  - degraded detail: `200`, `meta.surfaces.trainer_dialog = degraded`,
    `links.workbench_detail = /trainer/sessions/trn-20260419-001`
  - unavailable list: `200`, `meta.surfaces.trainer_dialog = unavailable`,
    `data = []`
  - unavailable detail for a known session projection: `200`,
    `meta.surfaces.trainer_dialog = unavailable`,
    `links.workbench_detail = /trainer/sessions/trn-known-001`
- Sibling front verification passed for the reviewed TW-01 slice:
  - `cd ../front-ai-trading-system && npx eslint src/pages/trainer/TeachingDialogList.tsx src/pages/trainer/TeachingDialogDetail.tsx src/pages/trainer/types.ts src/lib/bffClient.ts src/App.tsx`
  - `cd ../front-ai-trading-system && npm run build`

## Decision

`TW-01-teaching-dialog` remains **blocked** for this review cycle.

The current local front publication commit is contract-aligned, the local
Pantheon app returns truthful degraded and unavailable TW-01 envelopes, and
`links.workbench_detail` matches a mounted owner screen in the current front
router. Pantheon still cannot close the loop because:

- the active operator-bff runtime is stale and does not expose the TW-01 route
  family over live HTTP, and
- the current TW-01 publication commit is still local-only rather than
  GitHub-visible on a remote branch.

Pantheon must refresh runtime truth through:

- `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`

After runtime refresh, the next front-owned step is to publish or push
`4d19e0f31104e87294e267e1e6e1bc36065bf961` (or a successor truthful commit)
so the canonical request pair and feedback bundle become GitHub-visible, then
re-run the live/deployed link verification.

## Required Follow-up

1. Pantheon runtime: refresh or redeploy the running operator-bff service so
   real HTTP exposes:
   - `POST /api/v1/trainer/sessions`
   - `GET /api/v1/trainer/sessions`
   - `GET /api/v1/trainer/sessions/{session_id}`
   - `POST /api/v1/trainer/sessions/{session_id}/message`
2. Pantheon runtime: after refresh, verify over real HTTP that degraded and
   unavailable TW-01 responses remain truthful and that
   `links.workbench_detail` resolves to `/trainer/sessions/{session_id}` in the
   live/deployed environment.
3. Front repo: push `4d19e0f31104e87294e267e1e6e1bc36065bf961` or republish the
   TW-01 bundle from another truthful Git-visible commit/branch, ensuring the
   canonical request pair, feedback bundle, and reviewed TW-01 UI files remain
   in the same published history.
4. If the refreshed live runtime diverges from the published TW-01 field shape,
   emit `.coordination/requests/TW-01-teaching-dialog-bff-gap.yaml` instead of
   introducing client-side fallback or shadow state.

## 2026-04-21 Publication Addendum

The front-owned publication blocker is now resolved.

- `git -C ../front-ai-trading-system fetch origin --prune`
- `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
  now shows:
  - `93a4b58891031442133a6966d0354ae216a80b72 refs/heads/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system branch -r --contains 4d19e0f31104e87294e267e1e6e1bc36065bf961`
  now returns:
  - `origin/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system ls-tree -r --name-only 4d19e0f31104e87294e267e1e6e1bc36065bf961 -- .coordination/requests/TW-01-teaching-dialog-ui-done.yaml .coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml docs/pantheon-feedback/TW-01-teaching-dialog src/pages/trainer/TeachingDialogList.tsx src/pages/trainer/TeachingDialogDetail.tsx src/pages/trainer/types.ts src/lib/bffClient.ts src/App.tsx`
  still confirms the immutable transport commit contains:
  - the canonical ui-done and frontend-feedback request pair
  - the full `docs/pantheon-feedback/TW-01-teaching-dialog/` bundle
  - the reviewed TW-01 UI files and route wiring

This resolves Required Follow-up item 3 from the original review. The only
remaining blocking work for `TW-01-teaching-dialog` is the runtime refresh
tracked at `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`.

## Residual Risk

- This review validated the current Pantheon app through local FastAPI
  `TestClient` probes and validated the active runtime only far enough to prove
  it is stale for TW-01 over live HTTP.
- No deployed browser session was exercised, so the deployed
  `links.workbench_detail` navigation path is still pending runtime
  confirmation.

## 2026-04-21 Runtime Refresh Approval Addendum

The runtime blocker from the original review is now resolved.

- Reviewer recheck on `2026-04-21T16:24Z` first confirmed there was no active
  `uvicorn` / `operator-bff` process serving `127.0.0.1:18001`, so the
  refreshed runtime had to be relaunched from the current Pantheon workspace
  before approval.
- After relaunch, `python3 -m pytest
  services/control-plane/bff/test_tw01_teaching_dialog_contract.py -q`
  returned `5 passed`.
- `GET http://127.0.0.1:18001/openapi.json` now exposes:
  - `/api/v1/trainer/sessions`
  - `/api/v1/trainer/sessions/{session_id}`
  - `/api/v1/trainer/sessions/{session_id}/message`
  - `/api/v1/trainer/sessions/{session_id}/preview`
  - `/api/v1/trainer/sessions/{session_id}/commit`
  - `/api/v1/trainer/sessions/{session_id}/discard`
  - plus the trainer replay read routes
- Reviewer-created live probe `POST /api/v1/trainer/sessions` on `18001`
  returned `200` with session `trn-20260421-004`, `session_type = trainer`,
  `status = active`, and `links.workbench_detail =
  /trainer/sessions/trn-20260421-004`.
- `GET /api/v1/trainer/sessions?persona_id=persona-alpha&status=active&page_size=2`
  returned `200` with `meta.surfaces.trainer_dialog = degraded` and preserved
  backend-owned `links.workbench_detail` hrefs.
- `POST /api/v1/trainer/sessions/trn-20260421-004/message` returned `200`
  with `accepted_at`, `sequence_number = 1`, `status = active`, and updated
  `session_summary.message_count = 1`.
- `GET /api/v1/trainer/sessions/trn-20260421-004` returned `200` with ordered
  `events[]`, `allowedActions.canSendMessage = true`, truthful degraded
  metadata, and `links.workbench_detail =
  /trainer/sessions/trn-20260421-004`.
- A controlled fallback-disabled probe on `127.0.0.1:18011` returned `200`
  for `GET /api/v1/trainer/sessions?persona_id=persona-alpha` with `data = []`
  and `meta.surfaces.trainer_dialog = unavailable`.

This clears the remaining live-runtime blocker for `TW-01-teaching-dialog`.
Deployed-environment browser QA remains a separate follow-up item, but it is
no longer a blocker for this runtime refresh task.

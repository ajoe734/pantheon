# TW-01 Teaching Dialog Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the refreshed TW-01 `ui-done` plus `frontend-feedback`
return from the sibling front workspace against the current TW-01 contract,
example payload, the local front publication commit
`4d19e0f31104e87294e267e1e6e1bc36065bf961`, the local BFF app, and the active
operator-bff runtime.

The local front publication commit is internally consistent:

- it removes the pending-BFF placeholder
- it contains the canonical TW-01 request pair and feedback bundle
- it keeps the Teaching Dialog screens on the live TW-01 route family only

The current Pantheon workspace still serves the published TW-01 route family
locally:

- `POST /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions/{session_id}`
- `POST /api/v1/trainer/sessions/{session_id}/message`

Targeted TW-01 contract tests pass, local FastAPI TestClient probes confirm
truthful degraded and unavailable TW-01 envelopes plus
`links.workbench_detail = /trainer/sessions/{session_id}`, and the reviewed
front router matches that href shape.

Pantheon does not need a new TW-01 endpoint or any shadow-state workaround in
this cycle. The remaining work is split across runtime and publication truth:

- Pantheon runtime must refresh the live operator-bff service so TW-01 is
  exposed over real HTTP and can be checked in the deployed environment.
- Front-end publication must make the reviewed TW-01 commit GitHub-visible on a
  remote branch or republish it from another truthful GitHub-visible commit.

## Delivered Findings

### 1. The refreshed front TW-01 screens are contract-aligned in the current local publication commit

Observed front publication commit:

- `4d19e0f31104e87294e267e1e6e1bc36065bf961`

Observed behavior in that commit:

- `TeachingDialogList.tsx` creates and lists sessions through the shared
  `tw01Api`
- optional `context_refs[]` remain preserved in the session composer
- `TeachingDialogDetail.tsx` renders backend-owned `events[]`, keeps
  `allowedActions.canSendMessage` as the write gate, and re-fetches detail from
  the backend after send instead of mutating transcript state locally
- degraded and unavailable `trainer_dialog` copy is present without falling
  back to Persona teaching history
- the commit contains:
  - `.coordination/requests/TW-01-teaching-dialog-ui-done.yaml`
  - `.coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml`
  - `docs/pantheon-feedback/TW-01-teaching-dialog/*`
  - the reviewed TW-01 UI files

Observed front verification:

- `cd ../front-ai-trading-system && npx eslint src/pages/trainer/TeachingDialogList.tsx src/pages/trainer/TeachingDialogDetail.tsx src/pages/trainer/types.ts src/lib/bffClient.ts src/App.tsx`
- `cd ../front-ai-trading-system && npm run build`
- Result: both passed; build kept only the existing non-blocking Vite
  chunk-size warning

Impact:

- the current TW-01 UI logic is no longer the blocker
- the remaining front blocker is GitHub-visible publication, not UI correctness

### 2. Local Pantheon validation now covers degraded and unavailable TW-01 surfaces

Observed local FastAPI TestClient verification:

- degraded list: `200 OK`, `meta.surfaces.trainer_dialog = degraded`,
  `links.workbench_detail = /trainer/sessions/trn-20260419-001`
- degraded detail: `200 OK`, `meta.surfaces.trainer_dialog = degraded`,
  `links.workbench_detail = /trainer/sessions/trn-20260419-001`
- unavailable list: `200 OK`, `meta.surfaces.trainer_dialog = unavailable`,
  `data = []`
- unavailable detail: `200 OK`, `meta.surfaces.trainer_dialog = unavailable`,
  `links.workbench_detail = /trainer/sessions/trn-missing-001`

Observed local route-table verification:

- the reviewed front router mounts `/trainer/sessions/:session_id`

Impact:

- Pantheon has now completed the local degraded/unavailable branch review
- `links.workbench_detail` is locally aligned to the mounted trainer detail
  route in the current front workspace

### 3. Live HTTP and deployed-environment verification are still blocked by runtime drift

Observed active runtime behavior at `http://127.0.0.1:18001`:

- `GET /api/v1/trainer/sessions?persona_id=persona-alpha` -> `404 Not Found`
- `GET /openapi.json` -> `200 OK`, but with no `/api/v1/trainer/*` paths

Impact:

- the requested live-BFF verification cannot close on the running runtime yet
- Pantheon emitted `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`
  instead of guessing about deployed truth

### 4. The refreshed front return is still not GitHub-visible from a remote branch

Observed remote-containment verification:

- `git -C ../front-ai-trading-system fetch origin --prune`
- `git -C ../front-ai-trading-system branch -r --contains 4d19e0f31104e87294e267e1e6e1bc36065bf961`
- Result: no remote branch contains the reviewed TW-01 commit

Impact:

- the current TW-01 return is locally replayable but not yet GitHub-visible
- the next front-owned step is to push or republish the reviewed TW-01 bundle,
  not to rewrite the screens

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Published endpoint family in the current workspace: live
- Pantheon delivery completed:
  - re-verified all four TW-01 routes locally through contract tests
  - validated degraded and unavailable TW-01 list/detail responses through
    FastAPI TestClient
  - confirmed local `links.workbench_detail` alignment with the mounted trainer
    detail route
  - recorded the runtime follow-up handoff for the stale live HTTP runtime
- Pantheon follow-up still required:
  - refresh or redeploy the active operator-bff runtime
  - verify the TW-01 route family plus `links.workbench_detail` over live HTTP
    and in the deployed environment
- Front follow-up still required:
  - push `4d19e0f31104e87294e267e1e6e1bc36065bf961` or republish the same TW-01
    bundle from another truthful GitHub-visible commit or branch
  - keep the canonical TW-01 request pair and feedback bundle together in that
    same published history
  - emit a `bff-gap` handoff instead of guessing if the refreshed live runtime
    payload diverges from the published contract
- Current loop outcome: `followup-required`

## Verification Performed

- Reviewed the returned Pantheon-local mirrored request artifacts:
  - `.coordination/requests/TW-01-teaching-dialog-ui-done.yaml`
  - `.coordination/requests/TW-01-teaching-dialog-frontend-feedback.yaml`
- Reviewed the canonical packet:
  - `docs/bff/TW-01-teaching-dialog.md`
  - `docs/examples/TW-01-teaching-dialog.json`
  - `docs/screens/TW-01-teaching-dialog.md`
  - `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the local front publication commit:
  - `4d19e0f31104e87294e267e1e6e1bc36065bf961`
- Re-reviewed the front implementation and feedback bundle at that commit
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_tw01_teaching_dialog_contract.py -q`
  - Result: `5 passed`
- Probed the current Pantheon BFF app locally through FastAPI TestClient for:
  - degraded list/detail TW-01 responses
  - unavailable list/detail TW-01 responses
  - `links.workbench_detail` route shape
- Verified the currently running operator-bff runtime over live HTTP:
  - `GET http://127.0.0.1:18001/api/v1/trainer/sessions?persona_id=persona-alpha`
  - `GET http://127.0.0.1:18001/openapi.json`
- Verified remote publication visibility:
  - `git -C ../front-ai-trading-system fetch origin --prune`
  - `git -C ../front-ai-trading-system branch -r --contains 4d19e0f31104e87294e267e1e6e1bc36065bf961`
- Replayed current sibling front verification:
  - `cd ../front-ai-trading-system && npx eslint src/pages/trainer/TeachingDialogList.tsx src/pages/trainer/TeachingDialogDetail.tsx src/pages/trainer/types.ts src/lib/bffClient.ts src/App.tsx`
  - `cd ../front-ai-trading-system && npm run build`

## Not Completed

- No deployed browser QA against a shared Pantheon environment was performed in
  this review cycle
- The active live HTTP operator-bff runtime has not yet been refreshed to the
  current Pantheon workspace code for TW-01
- The reviewed TW-01 front commit is not yet published on any remote branch

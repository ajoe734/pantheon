# PKT-010 Runtime State Board Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml` against the
current PKT-010 contract, example payload, coordination replay rules, the
current `frontend-feedback` request, the sibling front implementation, and the
local Pantheon BFF app.

The core PKT-010 read surface is live in the current workspace:

- Pantheon serves `GET /api/v1/operator/runtime-state`
- targeted PKT-010 contract tests pass
- a direct local route probe returns `200 OK` with truthful degraded metadata
  in the current dataset

No new endpoint or client-side shadow state is authorized in this cycle.

The loop still cannot close for two concrete reasons:

1. the front-owned request pair is still not replayable from the exact
   immutable commit it advertises
2. Pantheon has not yet locked truthful deployed semantics for
   `rollback_summary.href`

## Verified UI Alignment

- `OperatorRuntimeStateBoard.tsx` reads the screen through
  `operatorApi.getRuntimeStateBoard()` and does not add raw component-level
  network calls.
- The board renders one runtime roster directly from `runtimes[]` and keeps
  sorting, filtering, and pagination server-backed.
- The page shows the shared degradation banner for degraded supporting surfaces
  and replaces the table when `meta.surfaces.runtime_state = unavailable`.
- Telemetry-null rows render explicit unavailable copy instead of browser joins
  against lower-level routes.

## Delivered Findings

### 1. Pantheon runtime-state route is live and exposes degradation honestly

Published PKT-010 contract:

- `GET /api/v1/operator/runtime-state`

Observed runtime result in the current workspace:

- `200 OK` from local FastAPI `TestClient`
- `meta.surfaces.runtime_state.status = degraded`
- `meta.surfaces.rollback_history.status = degraded`

Observed automated verification:

- `python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q`
- Result: `3 passed`

Impact:

- the reviewed screen can load its primary data path against the current
  Pantheon runtime
- degraded and unavailable route behavior is still covered by targeted
  Pantheon contract tests, even though the current workspace dataset only
  surfaced the degraded branch during direct probing

### 2. The GitHub-visible transport is still not replay-clean

The current sibling request pair is published from front HEAD
`2779d237736b6a1d02ef0e4a4c4f54a7983bb70c` but advertises
`source_commit = be42f22c2388076af4bb7b1f1d4209aaf90af6a8`.

Pantheon verified that commit `be42f22c2388076af4bb7b1f1d4209aaf90af6a8`
contains the reviewed PKT-010 UI files and feedback bundle, but it does not
contain the current request bodies that point at `be42f22...`; those YAML edits
landed later in `2779d237736b6a1d02ef0e4a4c4f54a7983bb70c`.

Impact:

- replay cannot reconstruct the exact current PKT-010 handoff set from the
  advertised front commit
- the front repo must republish the request pair from one truthful immutable
  commit before PKT-010 can close

### 3. PKT-010 rollback href semantics still are not locked truthfully

Published PKT-010 contract wording:

- `plan_ref.href` points to the deployment review owner screen
- `rollback_summary.href` points to rollback history

Current Pantheon BFF payload shaping:

- `plan_ref.href = /operator/deployment-review?plan={plan_id}`
- `rollback_summary.href = /api/v1/runtimes/{runtime_id}/rollbacks`

Current sibling app state:

- the reviewed screen renders payload refs verbatim
- front HEAD added a React route alias for
  `/api/v1/runtimes/:runtimeId/rollbacks`
- the advertised `source_commit` does not yet include the later request-pair
  repoint that references that route-fix ancestor

Impact:

- the reviewed frontend remains correct to render the payload href verbatim
- Pantheon still must decide whether the API-looking rollback href is the
  intended deployed owner-screen target or whether the contract should be
  revised to publish a clearer browser-owned route

## Pantheon-Side Outcome

- Pantheon contract: unchanged in this review cycle
- Published endpoint: already live in the current workspace
- Pantheon delivery completed:
  - recorded the updated review packet and delivery lock for PKT-010
  - kept the Lovable task in `followup-required`
- Front follow-up still required:
  - republish the request pair from one truthful immutable commit
- Pantheon follow-up still required:
  - resolve the `rollback_summary.href` truth boundary for deployed operator
    navigation
- Current loop outcome: `delivered` on the Pantheon backend-delivery record;
  packet loop remains open in the next follow-up cycle

## Verification Performed

- Reviewed Pantheon-visible request artifacts:
  - `.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml`
  - `.coordination/requests/PKT-010-runtime-state-board-frontend-feedback.yaml`
- Reviewed the canonical packet:
  - `docs/bff/PKT-010-runtime-state-board.md`
  - `docs/screens/PKT-010-runtime-state-board.md`
  - `docs/examples/PKT-010-runtime-state-board.json`
  - `docs/pantheon-handoffs/PKT-010-runtime-state-board/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front implementation:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/operator/OperatorRuntimeStateBoard.tsx`
  - `src/pages/operator/types.ts`
- Ran sibling front validation:
  - `npm run build`
  - Result: passed
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q`
  - Result: `3 passed`
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient`
  and confirmed `GET /api/v1/operator/runtime-state` returns `200 OK` with
  degraded runtime-state and rollback-history metadata in the current
  workspace

## Not Completed

- No deployed-environment browser QA was performed in this review cycle
- The current workspace dataset did not expose an `unavailable`
  `runtime_state` response without test shaping; that branch is covered by
  targeted contract tests rather than the direct route probe

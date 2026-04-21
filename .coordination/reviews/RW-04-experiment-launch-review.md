# RW-04 Experiment Launch Review Packet

## Date

2026-04-21

## Reviewer

Codex

## Findings

### 1. High: the returned RW-04 handoff is still not replayable from a GitHub-visible front branch

- The sibling front repo now has two immutable local commits on
  `pkt-004-detail-fix`:
  - `6e17dd8f233dad31d20b64781f23063e00ddde54`
    (`EXEC-FRONT-RW04-001 publish RW-04 experiment launch UI`)
  - `147297bf03707bf05d6fe0aab4a30c8d84599c5b`
    (`EXEC-FRONT-RW04-001 publish RW-04 coordination request pair with immutable SHA`)
- `git -C ../front-ai-trading-system ls-tree -r --name-only 6e17dd8f233dad31d20b64781f23063e00ddde54 -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
  confirms `6e17dd8f233dad31d20b64781f23063e00ddde54` contains the RW-04 pages,
  feedback bundle, shared client wiring, and route wiring, but not the
  canonical request pair.
- `git -C ../front-ai-trading-system ls-tree -r --name-only 147297bf03707bf05d6fe0aab4a30c8d84599c5b -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
  confirms `147297bf03707bf05d6fe0aab4a30c8d84599c5b` contains the full request
  pair, feedback bundle, and reviewed UI files together, and the request pair
  now uses immutable `source_commit: 6e17dd8f233dad31d20b64781f23063e00ddde54`
  instead of `HEAD`.
- The publication is still not GitHub-visible. On April 21, 2026,
  `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
  returned remote head `93a4b58891031442133a6966d0354ae216a80b72`, and
  `git -C ../front-ai-trading-system ls-tree -r --name-only 93a4b58891031442133a6966d0354ae216a80b72 -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
  returned only:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`
  - `src/lib/bffClient.ts`
- Impact: the runtime blocker is resolved and the mutable-`HEAD` blocker is
  narrowed to immutable local commits, but the loop still cannot close because
  the coordination bus would still replay the front branch from remote head
  `93a4b58891031442133a6966d0354ae216a80b72`, which omits the RW-04 request
  pair, feedback bundle, and experiment pages.

## Reviewed Artifacts

- Pantheon contract bundle:
  - `docs/bff/RW-04-experiment-launch.md`
  - `docs/examples/RW-04-experiment-launch.json`
  - `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/RW-04-experiment-launch-contract-ready.yaml`
  - `.coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml`
- Pantheon coordination state:
  - `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  - `.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
  - `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`
  - `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`
- Reviewed sibling front workspace:
  - `../front-ai-trading-system/.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-04-experiment-launch/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-04-experiment-launch/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-04-experiment-launch/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-04-experiment-launch/QA_STATUS.md`
  - `../front-ai-trading-system/src/pages/research/ExperimentLaunch.tsx`
  - `../front-ai-trading-system/src/pages/research/ExperimentDetail.tsx`
  - `../front-ai-trading-system/src/pages/research/ExperimentHistory.tsx`
  - `../front-ai-trading-system/src/pages/research/ExperimentRunHelpers.ts`
  - `../front-ai-trading-system/src/pages/research/ExperimentRunView.tsx`
  - `../front-ai-trading-system/src/pages/research/ExperimentTypes.ts`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx`
- Front Git publication checks:
  - `git -C ../front-ai-trading-system show --stat --oneline 6e17dd8f233dad31d20b64781f23063e00ddde54 -- docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
  - `git -C ../front-ai-trading-system show --stat --oneline 147297bf03707bf05d6fe0aab4a30c8d84599c5b -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
  - `git -C ../front-ai-trading-system ls-tree -r --name-only 6e17dd8f233dad31d20b64781f23063e00ddde54 -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
  - `git -C ../front-ai-trading-system ls-tree -r --name-only 147297bf03707bf05d6fe0aab4a30c8d84599c5b -- .coordination/requests/RW-04-experiment-launch-ui-done.yaml .coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml docs/pantheon-feedback/RW-04-experiment-launch src/pages/research/ExperimentLaunch.tsx src/pages/research/ExperimentDetail.tsx src/pages/research/ExperimentHistory.tsx src/pages/research/ExperimentRunHelpers.ts src/pages/research/ExperimentRunView.tsx src/pages/research/ExperimentTypes.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx`
  - `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
- Pantheon runtime verification:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_rw04_experiment_launch_contract.py`
  - `http://127.0.0.1:18001`
  - `http://127.0.0.1:18012`

## Verified Positives

- The previously reported RW-04 front-owned contract bugs remain fixed in the
  current local publication chain:
  - `ExperimentRunHelpers.ts` treats route-not-live as
    `404 && code in {NOT_FOUND, ROUTE_NOT_FOUND}` instead of collapsing all
    404s.
  - `ExperimentLaunch.tsx` keeps the polling timer active after an initial
    detail-read failure and surfaces a retry CTA while waiting for the first
    durable snapshot.
  - `ExperimentHistory.tsx` clears `pageHistory` when the `ticket_id` filter
    changes, matching the status-filter reset path.
- The reviewed front workspace still mounts the requested RW-04 routes:
  - `/research/experiments`
  - `/research/experiments/launch`
  - `/research/experiments/:experiment_id`
- The front publication is internally consistent locally:
  - UI implementation commit
    `6e17dd8f233dad31d20b64781f23063e00ddde54` pins the reviewed RW-04 pages,
    shared client wiring, and feedback bundle.
  - Request-pair publication commit
    `147297bf03707bf05d6fe0aab4a30c8d84599c5b` adds the canonical
    `.coordination/requests/RW-04-experiment-launch-ui-done.yaml` and
    `.coordination/requests/RW-04-experiment-launch-frontend-feedback.yaml`
    files with immutable `source_commit: 6e17dd8f233dad31d20b64781f23063e00ddde54`.
- The front slice routes RW-04 traffic only through the shared client helpers
  in `src/lib/bffClient.ts`; no component-level raw `fetch` path was
  introduced.
- Cancel CTA visibility still follows Pantheon authority and degradation
  semantics: `allowedActions.canCancel` is required and the CTA is suppressed on
  degraded or unavailable status surfaces.
- Live operator-auth verification passes for the active runtime:
  - `GET /openapi.json` on `http://127.0.0.1:18001` advertises all four RW-04
    routes.
  - `GET /api/v1/experiments?page_size=5` returned `200` with
    `meta.surfaces.experiment_history = degraded`.
  - `GET /api/v1/experiments/exp-20260419-012` returned a completed detail
    payload with `allowedActions.canCancel = false`.
  - `GET /api/v1/experiments/exp-20260418-009` returned a running detail
    payload with `allowedActions.canCancel = true`.
  - `GET /api/v1/experiments/exp-20260417-004` returned a failed detail payload
    with populated `failure.reason_code` and `failure.message`.
  - `GET /api/v1/experiments/does-not-exist` returned `404 OBJECT_NOT_FOUND`.
  - `POST /api/v1/experiments/launch` returned queued
    `exp-20260421-004`; `POST /api/v1/experiments/exp-20260421-004/cancel`
    returned `status = canceled` with `allowedActions.canCancel = false`; and
    follow-up detail/list probes confirmed the durable terminal canceled state.
- Workspace-backed unavailable verification also passes:
  - a local uvicorn probe on `http://127.0.0.1:18012` with
    `BFF_READ_SURFACE_STATE=unavailable` returned `200` list data `[]` with
    `meta.surfaces.experiment_history = unavailable`
  - the same probe returned `200` detail for `exp-20260418-009` with
    `meta.surfaces.experiment_status = unavailable` while preserving the
    backend-owned run snapshot
- Pantheon contract verification remains green:
  - `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
    passed with `21 passed`
- Sibling front verification passed for the current RW-04 slice:
  - `npx eslint ...`
  - `npx tsc --noEmit`
  - `npm run build`

## Decision

Follow-up required. `RW-04-experiment-launch` is not loop-complete.

Pantheon has revalidated the RW-04 route family over HTTP, including the
published OpenAPI exposure, degraded live behavior, unavailable HTTP behavior
through a workspace-backed probe, and the full
queued/running/completed/failed/canceled plus `OBJECT_NOT_FOUND` response
spread. The earlier front-owned contract bugs are also fixed and the front repo
now has immutable local publication commits. The remaining blocker is
GitHub-visible publication truth: the local request-pair commit
`147297bf03707bf05d6fe0aab4a30c8d84599c5b` has not been pushed, and remote head
`93a4b58891031442133a6966d0354ae216a80b72` still omits the RW-04 request pair,
feedback bundle, and experiment pages.

Pantheon follow-up remains required through:

- `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`

The prior runtime blocker tracked in
`.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml` is now
resolved.

## Required Follow-up

1. Front repo: push local commits
   `6e17dd8f233dad31d20b64781f23063e00ddde54` and
   `147297bf03707bf05d6fe0aab4a30c8d84599c5b`, or a later equivalent
   publication chain, to `origin/pkt-004-detail-fix` so the RW-04 request pair,
   feedback bundle, and reviewed UI files become GitHub-visible.
2. Front repo: keep the now-fixed detail 404 handling, first-detail retry
   behavior, history pagination reset, and immutable `source_commit`
   (`6e17dd8f233dad31d20b64781f23063e00ddde54`) in whatever pushed publication
   Pantheon reviews next.
3. Deployed browser verification against the mounted experiment routes remains
   non-blocking after the GitHub-visible publication lands.

## Residual Risk

- Pantheon's remaining risk is publication transport rather than contract drift
  or runtime freshness. The route family is live and verified over HTTP in
  degraded and unavailable modes, and the front repo now has immutable local
  commits for both the reviewed UI and the canonical request pair, but the
  current remote branch still points at a pre-RW04 tree.

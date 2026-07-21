# PKT-003 Post-Incident Review Console Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Scope

Review the dispatched `PKT-003-post-incident-review` `ui-done` handoff against
the published PKT-003 contract lock, example fixture, PKT-005 degradation
banner and SSE rules, and the clean `origin/main` verification checkout at
`/tmp/front-origin-main-verify`.

## Verification Performed

- Resolved the current front `origin/main` / `HEAD` to
  `7f2fbbeefc988eb2ef30d1fed5edb0918ad5276f`.
- Confirmed the tracked PKT-003 publication set at that commit:
  - present:
    `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`
  - absent:
    `.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml`
  - absent:
    `docs/pantheon-feedback/PKT-003-post-incident-review/`
- Confirmed the published `ui-done` payload still advertises
  `source_commit: HEAD` instead of an immutable commit SHA.
- Verified the actual shared BFF client wiring:
  - `src/lib/bffClient.ts:936-948` calls
    `GET /api/v1/operator/post-incident-review/{incident_id}`
  - `PostIncidentReviewConsole.tsx` still uses
    `operatorApi.listIncidents()` for `GET /api/v1/incidents`
- Verified the current PKT-003 fixture and contract lock still use
  object-wrapped `meta.surfaces.<surface>.status` envelopes, and the reviewed
  banner helper narrows `status`, not `state`.
- Re-ran targeted static validation in the isolated front worktree:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx`
- Attempted `npm run build` in `/tmp/front-origin-main-verify`; Vite failed to
  resolve `i18next` from `src/i18n/index.ts` in this verification checkout, so
  production build proof could not be refreshed in this pass.

## Findings

1. `../front-ai-trading-system/.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`
   is the only tracked PKT-003 handoff artifact at current front
   `origin/main` head `7f2fbbeefc988eb2ef30d1fed5edb0918ad5276f`. The paired
   `frontend-feedback` request and the required
   `docs/pantheon-feedback/PKT-003-post-incident-review/` bundle are both
   absent, and the published `ui-done` still uses symbolic
   `source_commit: HEAD` (`lines 1-5`). The current handoff is therefore not
   Git-replayable or protocol-complete.
2. `../front-ai-trading-system/src/pages/operator/PostIncidentReviewConsole.tsx`
   still validates and renders against a non-canonical staleness shape.
   `getDetailMissingFields()` requires `meta.staleness.reason`
   (`lines 212-218`), the lineage fallback copy renders
   `detailMeta.staleness.reason` (`lines 1087-1089`), and
   `../front-ai-trading-system/src/pages/operator/types.ts` still models
   `PostIncidentReviewStaleness` as `{ reason: string; served_from: string }`
   (`lines 143-146`). The current PKT-003 / PKT-005 lock only guarantees
   `served_from`, with optional `last_known_at` and `max_age_minutes`.
3. `../front-ai-trading-system/src/pages/operator/PostIncidentReviewConsole.tsx`
   still leaves the inherited PKT-005 host-screen SSE follow-up incomplete.
   Accepted `incident_updated` events only mutate list rows
   (`lines 479-491`) while the visible detail badge continues to read from the
   unchanged selected detail object (`lines 822-826`), the early
   `if (result.type !== 'accepted') return;` branch drops live reconciler
   `{ type: "bff-gap" }` results (`lines 479-480`), and the footer still shows
   connection state only (`lines 1196-1207`) instead of the required delayed
   update note.

## Verified Positives

- The actual detail endpoint wiring is still contract-correct. Although the
  `ui-done` summary string cites
  `GET /api/v1/incidents/{id}/post-review`, the implementation in
  `src/lib/bffClient.ts:936-948` uses the canonical
  `GET /api/v1/operator/post-incident-review/{incident_id}` route, matching the
  PKT-003 contract and example payload.
- The reviewed screen and degradation helper still consume
  `meta.surfaces.<surface>.status` object envelopes:
  - `src/pages/operator/PostIncidentReviewConsole.tsx:195-208`
  - `src/lib/degradationBanner.ts:64-79`
  - `src/lib/degradationBanner.ts:136-145`
  This remains aligned with
  `docs/examples/PKT-003-post-incident-review-console.json` and
  `docs/pantheon-delivery/PKT-003-post-incident-review/CONTRACT_LOCK.md`.
  Do not flip this screen to `meta.surfaces.<surface>.state` without a new
  Pantheon contract rebaseline.
- The screen is still mounted at `/operator/post-incident-review` and remains
  reachable from the Operator Console sidebar group.
- TypeScript compile and the shared degradation banner tests both pass in the
  isolated verification checkout.

## Decision

`PKT-003-post-incident-review` remains **follow-up-required**.

No new Pantheon endpoint, payload expansion, or contract rebaseline is required
for this cycle. The current blockers are front-owned publication hygiene plus
front-owned PKT-005 contract usage on this screen.

## Remaining Follow-up

1. Front repo publish the missing canonical
   `.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml`
   and `docs/pantheon-feedback/PKT-003-post-incident-review/` bundle.
2. Front repo replace `source_commit: HEAD` in the published `ui-done` request
   with the immutable commit SHA that contains the reviewed PKT-003 UI files.
3. Front repo align `PostIncidentReviewConsole.tsx` and
   `src/pages/operator/types.ts` to the locked staleness shape:
   `served_from` required, `last_known_at` and `max_age_minutes` optional, no
   required `reason`.
4. Front repo finish the inherited PKT-005 host-screen follow-up on this
   surface: reconcile accepted `incident_updated` events into the selected
   detail state, surface live reconciler `bff-gap` results, and add the
   required delayed-update note.
5. Re-run production build verification after the front verification checkout
   has a resolved dependency install, then redispatch Pantheon review on the
   unchanged PKT-003 contract.

## 2026-04-24 Working-Tree Follow-up Addendum

Pantheon re-reviewed the current sibling `front-ai-trading-system` working tree
on branch `pkt-004-detail-fix` after the PKT-003 follow-up refresh was applied
locally on top of base commit `139081f0e4d516494819003bd95968ecb9b86c99`.

### Verification performed

- Read the refreshed front-owned request pair and feedback bundle in the
  sibling working tree:
  - `../front-ai-trading-system/.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-003-post-incident-review/{LOVABLE_CHANGE_FEEDBACK.md,QA_STATUS.md,UI_DECISIONS.md}`
- Inspected the current PKT-003 implementation refresh:
  - `../front-ai-trading-system/src/pages/operator/PostIncidentReviewConsole.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
  - `../front-ai-trading-system/src/lib/degradationBanner.ts`
  - `../front-ai-trading-system/src/lib/sseReconciler.ts`
  - `../front-ai-trading-system/src/lib/sseClient.ts`
- Re-ran targeted static verification in the sibling front workspace:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/pages/operator/PostIncidentReviewConsole.tsx src/pages/operator/types.ts src/lib/degradationBanner.ts src/lib/sseReconciler.ts src/lib/sseClient.ts`
  - `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx`

### Findings

#### 1. The PKT-005 staleness and host-screen SSE follow-up is now resolved in the current front working tree

- `PostIncidentReviewConsole.tsx` no longer requires
  `meta.staleness.reason`; it now accepts canonical PKT-005
  `served_from` with optional `last_known_at` and `max_age_minutes`.
- `src/pages/operator/types.ts` now models
  `PostIncidentReviewStaleness` with the canonical field set:
  - `served_from`
  - optional `last_known_at`
  - optional `max_age_minutes`
- The lineage empty/degraded copy now renders canonical staleness guidance from
  those fields instead of assuming a required `reason`.
- Accepted `incident_updated` SSE events now reconcile into both the resolved
  incident row set and the selected detail summary state.
- Live reconciler `bff-gap` results now surface an explicit
  `PKT-005-sse-substrate-bff-gap` alert instead of being dropped after
  validation.
- The host-screen footer now keeps the delayed-update note explicit:
  "Real-time updates may be delayed."
- Targeted TypeScript compile, targeted ESLint, and the shared degradation
  banner regression test all passed in the sibling front workspace.

#### 2. The refreshed PKT-003 handoff is still not replay-clean because the reviewed changes are only in the working tree

- The refreshed request pair now truthfully declares that the reviewed follow-up
  lives in the current workspace on top of
  `139081f0e4d516494819003bd95968ecb9b86c99`, but no new front commit has been
  created yet.
- The reviewed request files, feedback-bundle updates, and PKT-003 source
  changes are therefore not reconstructible from a Git-visible immutable
  commit.
- Pantheon's remaining blocker is reduced to publication hygiene only: one
  truthful front commit must contain the refreshed PKT-003 request pair, the
  refreshed feedback bundle, and the reviewed PKT-003 UI files, and both
  request bodies must point `source_commit` at that exact commit SHA.

### Updated decision

`PKT-003-post-incident-review` remains **follow-up-required**, but only for the
commit-backed republish.

No new Pantheon endpoint, payload expansion, or contract rebaseline is required
for this cycle. The staleness-shape and host-screen SSE blockers named in the
original 2026-04-24 review are resolved in the current front working tree.

### Remaining follow-up

1. Front repo: create one immutable commit that contains:
   - `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`
   - `.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml`
   - `docs/pantheon-feedback/PKT-003-post-incident-review/`
   - `src/pages/operator/PostIncidentReviewConsole.tsx`
   - `src/pages/operator/types.ts`
2. Front repo: set both request `source_commit` values to that exact commit
   SHA and republish the packet through Git-visible history.
3. After that republish, redispatch Pantheon review so the transport tuple can
   be closed or approved from Git-visible evidence.
4. Live browser QA and live SSE timing verification remain deferred
   non-blocking follow-up after the republish.

## 2026-04-24 Remote Publish Addendum

Re-reviewed after front publish commit
`1df4a64047055ca3ea802d61c1df78211884aee2` was pushed to
`origin/pkt-004-detail-fix`.

### Verification performed

- Fetched current front remote refs and confirmed:
  - `git -C ../front-ai-trading-system rev-parse origin/pkt-004-detail-fix`
    -> `1df4a64047055ca3ea802d61c1df78211884aee2`
  - `git -C ../front-ai-trading-system branch -r --contains 1df4a64047055ca3ea802d61c1df78211884aee2`
    returns `origin/pkt-004-detail-fix`
  - `git -C ../front-ai-trading-system branch -r --contains c9b03d7ba1439db4f956c56106925675a98f8512`
    also returns `origin/pkt-004-detail-fix`
- Re-read the remote-visible request pair and feedback metadata:
  - `git -C ../front-ai-trading-system show origin/pkt-004-detail-fix:.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`
  - `git -C ../front-ai-trading-system show origin/pkt-004-detail-fix:.coordination/requests/PKT-003-post-incident-review-frontend-feedback.yaml`
  - `git -C ../front-ai-trading-system show origin/pkt-004-detail-fix:docs/pantheon-feedback/PKT-003-post-incident-review/API_GAP_REQUESTS.json`
- Re-ran targeted static verification in `../front-ai-trading-system`:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/pages/operator/PostIncidentReviewConsole.tsx src/pages/operator/types.ts src/lib/degradationBanner.ts src/lib/sseReconciler.ts src/lib/sseClient.ts`
  - `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx`
- Inspected the publish delta:
  - `git -C ../front-ai-trading-system diff --name-only c9b03d7ba1439db4f956c56106925675a98f8512 1df4a64047055ca3ea802d61c1df78211884aee2`

### Findings

None.

### Closeout notes

- The replay blocker is resolved. The remote-visible PKT-003 request pair now
  points both `source_commit` values at reviewed source commit
  `c9b03d7ba1439db4f956c56106925675a98f8512`, and both that reviewed source
  commit plus publish commit `1df4a64047055ca3ea802d61c1df78211884aee2` are
  reachable on `origin/pkt-004-detail-fix`.
- `docs/pantheon-feedback/PKT-003-post-incident-review/API_GAP_REQUESTS.json`
  on the remote branch now records
  `reviewed_source_commit: c9b03d7ba1439db4f956c56106925675a98f8512` with
  `status: "no_open_gaps"`.
- The republish is metadata-only relative to the reviewed UI snapshot. The
  delta from `c9b03d7ba1439db4f956c56106925675a98f8512` to
  `1df4a64047055ca3ea802d61c1df78211884aee2` only touches the two request
  files and refreshed feedback metadata files; the reviewed PKT-003 UI files
  remain pinned to source commit `c9b03d7ba1439db4f956c56106925675a98f8512`.
- Targeted TypeScript compile, targeted ESLint, and the shared degradation
  banner regression test all passed again after the remote publish.

### Final decision

`PKT-003-post-incident-review` is now **replay-clean and contract-aligned**.

No new Pantheon endpoint, payload expansion, or contract rebaseline is
required for this cycle. Remaining follow-up is non-blocking runtime/browser
QA only.

## 2026-04-24 Live Runtime Data Addendum

Pantheon then checked the active operator-bff runtime on
`http://127.0.0.1:18001` to validate the returned selected-detail and
incident-stream follow-up path against live HTTP rather than only the local
contract harness.

### Verification performed

- Verified the local Pantheon incident contract slice still passes:
  - `python3 -m pytest -q services/control-plane/bff/smoke_test_incident.py`
- Queried the active runtime OpenAPI document:
  - `curl -sS http://127.0.0.1:18001/openapi.json | jq -r '.paths | keys[]'`
- Probed the live resolved-incident list under operator auth:
  - `curl -i -sS -H 'Authorization: Bearer test-operator:operator' 'http://127.0.0.1:18001/api/v1/incidents?status=resolved&page_size=5'`
- Probed the live post-incident detail route under operator auth:
  - `curl -i -sS -H 'Authorization: Bearer test-operator:operator' 'http://127.0.0.1:18001/api/v1/operator/post-incident-review/inc-20260409-002?snapshot=preferred'`
  - `curl -i -sS -H 'Authorization: Bearer test-operator:operator' 'http://127.0.0.1:18001/api/v1/operator/post-incident-review/inc-20260410-001?snapshot=preferred'`

### Findings

1. The remote publication and local contract proof remain valid.
   - `smoke_test_incident.py` still passes (`20 passed`), so the current
     Pantheon workspace continues to serve the resolved-incident list,
     postmortem, post-incident review, and RBAC slices locally.
   - `/openapi.json` on 18001 advertises `/api/v1/incidents`,
     `/api/v1/incidents/stream`, and
     `/api/v1/operator/post-incident-review/{incident_id}`.
2. The active runtime still lacks resolved-incident data for live PKT-003
   detail and SSE validation.
   - `GET /api/v1/incidents?status=resolved&page_size=5` returned `200` with
     `items[] = []`.
   - Direct detail probes for `inc-20260409-002` and `inc-20260410-001`
     returned `404 OBJECT_NOT_FOUND`.
   - Pantheon therefore could not exercise the selected-detail or
     `/api/v1/incidents/stream` follow-up path over live data without
     introducing local shadow seed state.

### Updated decision

`PKT-003-post-incident-review` remains **approved on front publication truth**,
but a Pantheon-owned runtime follow-up is still required before claiming live
detail and SSE validation.

### Remaining follow-up

1. Runtime-worker: refresh the active 18001 runtime or its backing incident and
   postmortem dataset so at least one resolved incident and matching detail
   payload are available for live PKT-003 probes.
2. Re-run live operator-auth list, detail, and incident-stream verification and
   hand control back to Pantheon review / front-sync.
3. Deployed browser QA remains non-blocking after the runtime follow-up.

# PKT-005 SSE Reconciliation Substrate — Backend Delivery Note

## Status

`cycle-2-dispatched`

## Summary

Pantheon reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-005-sse-substrate-ui-done.yaml` against the
published PKT-005 SSE contract, screen spec, example fixtures, and the sibling
`front-ai-trading-system` checkout.

This review is now anchored to a replayable front implementation commit:

- payload `source_commit`: `c08acb3ea59f4c56ced578820aa6a5129a309de1`
- front repo publication commit carrying the corrected `ui-done` artifact:
  `37ebcafacb68ff617f097271c46eaac4a478cbb8`
- original worker dispatch payload named the stale pre-rebase hash
  `18d52e8d462c250da31a7568552f2ad1c4742905`

The shared SSE files and feedback bundle are present in the sibling repo, an
archived `c08acb3...` snapshot builds successfully, the touched files pass
targeted ESLint on that snapshot, and the published reconnect fixture confirms
reconciler duplicate-skip behaviour.

Two positive contract checks also passed in the reviewed tree:

- `kill_switch_activated` correctly gates CTA availability on `IncidentDetail`
  and the shared `IncidentActionDrawer`
- degradation-banner state remains derived from composed-view BFF `meta`
  snapshots rather than SSE payload fields

No Pantheon BFF gap or contract expansion is required for this cycle. The next
UI cycle must stay on the existing PKT-005 contract.

The reviewed handoff still cannot be accepted as complete because several
contract-required behaviours remain missing or miswired in the front repo, and
the published `ui-done` transport is still not replayable from its own
advertised `source_commit`.

## Verified Pantheon Contract

- Runtime stream remains `GET /api/v1/runtime/{runtime_id}/events/stream`
- Incident stream remains `GET /api/v1/incidents/stream`
- Kill-switch stream remains `GET /api/v1/kill-switch/updates`
- `last_event_id` replay semantics remain as published in
  `docs/bff/PKT-005-sse-substrate.md`
- No new endpoint, no shadow state, and no Pantheon-side contract expansion is
  authorized in this follow-up

## Verified Front Publication State

- The sibling front repo contains the reviewed shared SSE files at
  `c08acb3ea59f4c56ced578820aa6a5129a309de1`.
- The sibling front repo contains the tracked PKT-005 feedback bundle under
  `docs/pantheon-feedback/PKT-005-sse-substrate/`.
- The corrected `ui-done` transport artifact is published at
  `37ebcafacb68ff617f097271c46eaac4a478cbb8`.
- The payload path is absent at the payload's own advertised `source_commit`
  `c08acb3ea59f4c56ced578820aa6a5129a309de1`; the request first appears in
  `942f921` and is corrected in `37ebcaf`. The current transport tuple
  therefore still breaks replay.
- The usual paired request
  `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml` is
  still absent in the sibling front repo and should be restored in the next UI
  cycle so the bus carries the standard review pair.

## Findings Requiring Another UI Cycle

### 1. The published `ui-done` request is still not replayable from its own transport commit

The phase-3 coordination loop spec requires `payload_path` to exist at the
transport envelope `source_commit`. The current request still advertises
`source_commit: c08acb3ea59f4c56ced578820aa6a5129a309de1`, but that commit does
not contain `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`.

Impact:

- the coordination payload is not replayable from the advertised tuple
- supervisor or manual replay tooling can fail when resolving the published
  request at `source_commit`
- the next UI cycle must republish the request pair from a commit that actually
  contains the payload paths

### 2. Replay state advances on receipt instead of successful apply

`src/lib/sseClient.ts` stores `event.id` as `last_event_id` before the event is
validated or applied by the reconciler. The PKT-005 contract requires replay to
resume from the most recently applied event.

Impact:

- a malformed event can advance the replay cursor even when the UI rejects it
- a reconnect can skip a required replay item instead of replaying it again
- the current implementation does not fully satisfy the published replay rule

### 3. The required 60-second delayed-update footer note is missing

The contract requires each host screen to show "Real-time updates may be
delayed" when no SSE event arrives for 60 seconds on a surface where updates
are expected. The reviewed host screens only render connection-state text in
their footers.

Affected screens:

- `src/pages/operator/DeploymentReviewConsole.tsx`
- `src/pages/operator/IncidentDetail.tsx`
- `src/pages/operator/PostIncidentReviewConsole.tsx`

Impact:

- the UI does not expose the stale-live-data hint required by PKT-005
- connection state and data-freshness state remain conflated

### 4. Several subscribed events are not applied to the visible host state

The screens subscribe to the correct streams, but some handlers treat accepted
events as informational no-ops instead of reconciling the visible state.

Examples:

- `DeploymentReviewConsole.tsx` accepts `runtime_state_changed` but performs no
  visible state mutation, even though the screen exposes runtime/deployment
  state through the selected detail panel.
- `IncidentDetail.tsx` shows the incident status badge from the composed view,
  but accepted `incident_updated` events are treated as informational only.
- `PostIncidentReviewConsole.tsx` shows the selected incident status in the
  detail panel, but accepted `incident_updated` events only mutate the list
  state and not the selected detail view.

Impact:

- the reviewed UI does not fully meet the "events are applied to UI state
  without a full-page refresh" acceptance criterion
- reconnect and replay correctness are weaker because visible state is not the
  thing being reconciled on all subscribed surfaces

### 5. Reconciler `bff-gap` results are dropped by the screen handlers

`SseReconciler.accept()` can return `{ type: 'bff-gap', missingFields }`, but
the reviewed host screens and drawer page return early on non-accepted results
without surfacing an alert state or emitting the required coordination handoff.

Affected handlers:

- `src/pages/operator/DeploymentReviewConsole.tsx`
- `src/pages/operator/IncidentDetail.tsx`
- `src/pages/operator/PostIncidentReviewConsole.tsx`
- `src/pages/operator/IncidentActionDrawerPage.tsx`

Impact:

- a live contract divergence in the SSE stream is silently swallowed at the
  screen boundary
- the implementation does not meet the PKT-005 rule to emit `bff-gap` instead
  of silently ignoring missing event fields

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon endpoints: unchanged
- Pantheon API gap: none requested in this cycle
- Next action: front repo must complete a new UI cycle on the existing PKT-005
  packet, fix the five deltas above, restore the paired
  `frontend-feedback` request, and republish the canonical `frontend-feedback`
  plus `ui-done` payloads from a transport-replayable commit

## Verification Performed

- Reviewed the incoming Pantheon-side request payload:
  - `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`
- Re-checked the Pantheon contract packet:
  - `docs/bff/PKT-005-sse-substrate.md`
  - `docs/examples/PKT-005-sse-substrate.json`
  - `docs/screens/PKT-005-sse-substrate.md`
  - `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`
- Reviewed the sibling front repo publication state:
  - `../front-ai-trading-system/.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md`
- Inspected the sibling front repo implementation files:
  - `src/lib/sseClient.ts`
  - `src/lib/sseReconnectManager.ts`
  - `src/lib/sseReconciler.ts`
  - `src/pages/operator/DeploymentReviewConsole.tsx`
  - `src/pages/operator/IncidentDetail.tsx`
  - `src/components/operator/IncidentActionDrawer.tsx`
  - `src/pages/operator/PostIncidentReviewConsole.tsx`
  - `src/pages/operator/types.ts`
- Ran archived-snapshot validation for `c08acb3...`:
  - `npm run build`
  - `npx eslint src/lib/sseClient.ts src/lib/sseReconnectManager.ts src/lib/sseReconciler.ts src/pages/operator/DeploymentReviewConsole.tsx src/pages/operator/IncidentDetail.tsx src/pages/operator/IncidentActionDrawerPage.tsx src/pages/operator/PostIncidentReviewConsole.tsx src/pages/operator/DeploymentPlanDetail.tsx src/components/operator/IncidentActionDrawer.tsx src/pages/operator/types.ts`
- Ran a fixture-based reconciler check in the sibling front repo:
  - `npx --yes tsx -e "...SseReconciler...docs/examples/PKT-005-sse-substrate.json..."`
  - result: all five example events were accepted once and the two replayed
    reconnect events were skipped as duplicates

## Not Completed

- No live browser QA or SSE stream smoke test was run in this review cycle
- No automated check was added for the delayed-update footer note or for the
  "store last_event_id only after apply" rule

## BP6-UI-REVIEW-004 Closure

**Status:** done  
**Finalized:** 2026-04-17  
**Finalized by:** Claude

Pantheon-side coordination loop for PKT-005-sse-substrate is officially closed.

- `lovable-ui-task` status: `loop-complete` (cycle 2)
- `backend-delivery` status: `cycle-2-dispatched`
- Integration commit: `42624e0e5d2b739cb39e5ee64870c668f85c408c`
- Review approved by Codex; no blocking findings

Next action remains on the front repo side: fix the five deltas and republish
from a transport-replayable commit per the `followup_expectation` in
`PKT-005-sse-substrate-backend-delivery.yaml`.

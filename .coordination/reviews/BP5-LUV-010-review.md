# BP5-LUV-010 Review Packet

## Date

2026-04-16

## Owner

Claude

## Reviewer

Codex

## Scope

Re-review the returned PKT-005 SSE substrate Lovable loop against the published
PKT-005 contract, example fixtures, front-repo publication requirements, and
the replayable frontend implementation at
`/home/lupin/code/front-ai-trading-system`.

## Reviewed Artifacts

- Pantheon request:
  - `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`
- Front repo publication:
  - `../front-ai-trading-system/.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md`
- Contract sources:
  - `docs/bff/PKT-005-sse-substrate.md`
  - `docs/screens/PKT-005-sse-substrate.md`
  - `docs/examples/PKT-005-sse-substrate.json`
  - `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`

## Verification Performed

- Confirmed frontend implementation commit
  `c08acb3ea59f4c56ced578820aa6a5129a309de1` exists and contains the shared SSE
  files plus all claimed screen wiring.
- Confirmed corrective artifact commit
  `37ebcafacb68ff617f097271c46eaac4a478cbb8` exists and updates the front-repo
  `ui-done` payload to the rebased implementation hash.
- Confirmed the original worker dispatch named
  `18d52e8d462c250da31a7568552f2ad1c4742905`, but the canonical front-repo
  `ui-done` artifact now points to `c08acb3ea59f4c56ced578820aa6a5129a309de1`.
- Ran `npm run build` successfully on an archived snapshot of
  `c08acb3ea59f4c56ced578820aa6a5129a309de1`.
- Ran targeted ESLint successfully on the same archived `c08acb3...` snapshot.
- Ran a fixture-based reconciler replay check against
  `docs/examples/PKT-005-sse-substrate.json`; all five example events were
  accepted once and the replayed reconnect events were skipped as duplicates.

## Findings

### 1. The returned `ui-done` payload is still not transport-replayable from its own `source_commit`

The front repo now contains the corrected `ui-done` payload, but the payload
still advertises:

- `source_commit: c08acb3ea59f4c56ced578820aa6a5129a309de1`

That commit contains the implementation files, but it does **not** contain the
published request payload path itself. Front-repo history shows:

- `c08acb3` — implementation commit
- `942f921` — add PKT-005 `ui-done` handoff artifact
- `37ebcaf` — correct the `source_commit` field inside the published `ui-done`
  artifact

The phase-3 coordination loop spec requires `payload_path` to exist at the
transport envelope `source_commit`. The current payload therefore still breaks
the replay tuple even after the ghost-commit fix.

### 2. The required front-owned `frontend-feedback` request is still missing

The front repo now contains the feedback directory and the corrected
`ui-done` request, but it still does **not** contain the required paired
request:

- missing:
  `../front-ai-trading-system/.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml`

The phase-3 coordination loop spec requires this machine-readable
`frontend-feedback` handoff before `ui-done`. The UI loop is therefore still
publication-incomplete even though the human-readable feedback files exist.

### 3. Replay cursor state still advances on receipt instead of successful apply

`src/lib/sseClient.ts` updates `lastEventId` as soon as a parsed event contains
an `id`, before the event is validated or accepted by the reconciler:

- `../front-ai-trading-system/src/lib/sseClient.ts:111-116`

That violates the PKT-005 rule that replay resumes from the most recently
**applied** event ID. A malformed or rejected event can currently advance the
replay cursor and cause reconnect to skip a required replay item.

### 4. The required 60-second delayed-update footer note is still absent

The PKT-005 screen spec requires a footer note saying "Real-time updates may be
delayed" when no event has arrived for 60 seconds on a live surface. The
reviewed screens render connection state only:

- `../front-ai-trading-system/src/pages/operator/DeploymentReviewConsole.tsx:592-603`
- `../front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:664-678`
- `../front-ai-trading-system/src/pages/operator/PostIncidentReviewConsole.tsx:1188-1199`

No timer- or freshness-based delayed-update note is implemented in the reviewed
tree.

### 5. Several accepted events are still treated as informational no-ops instead of reconciling visible host state

The contract requires accepted SSE events to reconcile visible UI state without
a full refresh. Several handlers explicitly accept events and then do not update
the visible host state:

- `DeploymentReviewConsole.tsx` accepts `runtime_state_changed` and does not
  mutate any visible deployment or runtime state:
  `../front-ai-trading-system/src/pages/operator/DeploymentReviewConsole.tsx:295-300`
- `IncidentDetail.tsx` accepts runtime and incident events but keeps both
  informational only while the screen displays incident status and action
  authority:
  `../front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:257-275`
- `PostIncidentReviewConsole.tsx` updates only the list row status and does not
  reconcile the selected detail state shown on screen:
  `../front-ai-trading-system/src/pages/operator/PostIncidentReviewConsole.tsx:468-484`

This does not satisfy the "events are applied to UI state without a full-page
refresh" acceptance requirement.

### 6. Live `bff-gap` results are still dropped by the screen handlers

`SseReconciler.accept()` can return `{ type: 'bff-gap', missingFields }`, but
the host handlers still return early on all non-accepted results instead of
surfacing the gap or emitting the required coordination handoff:

- `../front-ai-trading-system/src/pages/operator/IncidentDetail.tsx:271-284`
- `../front-ai-trading-system/src/pages/operator/PostIncidentReviewConsole.tsx:471-484`
- `../front-ai-trading-system/src/pages/operator/IncidentActionDrawerPage.tsx:63-70`

The current UI therefore still swallows live SSE contract divergence instead of
raising the required `bff-gap`.

## Confirmed Positives

- All reviewed component-level stream wiring goes through the shared
  `SseClient`; there are no raw `EventSource` calls in the component files.
- Reconnect backoff and deduplication are implemented and the example replay
  fixture does skip duplicates as expected.
- `kill_switch_activated` CTA gating is correctly wired:
  - `IncidentDetail.tsx` gates action CTAs through
    `killSwitchActivatedViaSse`: `:153`, `:227-228`, `:285-288`, `:634-655`
  - `IncidentActionDrawer.tsx` gates submit buttons through the propagated
    `killSwitchActivated` prop: `:315-316`, `:409-420`, `:781-784`
- The degradation banner is still derived from full BFF `meta` snapshots rather
  than SSE payload fields.

## Decision

`BP5-LUV-010` is **not approved yet**.

The corrected frontend commit is real, the shared SSE layer is present, build
and targeted lint succeed, and replay deduplication works. But the returned
loop still fails both the closed-loop publication requirement
(`frontend-feedback` request missing) and multiple PKT-005 contract/acceptance
requirements.

## Required Follow-up Before Re-review

1. Republish the front-owned request pair from a transport-replayable commit:
   the commit referenced by the transport `source_commit` must actually contain
   the published request payload path.
2. Publish the missing
   `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml`.
3. Move `last_event_id` persistence behind successful reconciler acceptance and
   apply.
4. Add the 60-second "Real-time updates may be delayed" footer note on the
   relevant live surfaces.
5. Reconcile accepted runtime or incident events into the visible host state,
   not informational no-ops.
6. Surface or emit `bff-gap` when `SseReconciler.accept()` reports missing live
   stream fields.
7. Republish the paired `frontend-feedback` and `ui-done` artifacts after those
   deltas are fixed.

## Cycle-2 Artifact Review Addendum

### Date

2026-04-16

### Reviewed Artifacts

- `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml`
- `.coordination/responses/PKT-005-sse-substrate-lovable-prompt.md`

### Verification

- Confirmed the cycle-2 `lovable-prompt` now carries all five frontend deltas
  from the original review findings:
  - `SseClient.markApplied` moves replay cursor advancement behind accepted apply
  - 60-second delayed-update footer note is required on the three live surfaces
  - accepted `runtime_state_changed` and `incident_updated` events are applied
    to visible host state
  - reconciler `bff-gap` results must be surfaced on all four affected screens
  - the missing `frontend-feedback` request is now part of the required output
- Tightened the publication instructions in the prompt and the `lovable-ui-task`
  acceptance so the next front-repo cycle must publish `frontend-feedback` and
  `ui-done` from a transport-replayable commit that actually contains both
  payload paths and uses that same commit as `source_commit`.

### Decision

`BP5-LUV-010` is **approved for dispatch**.

This addendum supersedes the earlier "not approved yet" decision for the
artifact-review stage of this task.

The five concrete frontend deltas are now explicit, and the Lovable artifacts
no longer leave the replayable publication rule implicit. Return to Claude for
finalization after `review_approved` is recorded.

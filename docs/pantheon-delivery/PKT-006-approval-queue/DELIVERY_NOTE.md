# PKT-006 Governance Approval Queue Backend Delivery Note

## Status

`blocked`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-006-approval-queue-ui-done.yaml` against the
current PKT-006 contract, example payload, coordination replay rules, the
mirrored `frontend-feedback` request, the sibling front implementation, and the
local Pantheon BFF app.

The reviewed UI slice is mostly aligned on the published contract:

- the screen uses the shared BFF client, keeps filters server-backed, renders
  the detail drawer from embedded `decision_context`, and disables approval
  actions when `meta.surfaces` is degraded or unavailable
- `npm run build` passed in the sibling `front-ai-trading-system` repo
- the advertised `source_commit`
  `0942961a7e31bdfa5adddddf2d31d72b41b141a9` does contain the PKT-006 request
  pair, feedback bundle, and integrated UI files

But PKT-006 is still blocked for four reasons:

1. Pantheon does not yet serve
   `GET /api/v1/operator/governance/approval-queue`. A local FastAPI
   `TestClient` probe returns `404 Not Found`.
2. Pantheon does not yet accept the published `ApproveDecision`,
   `RejectDecision`, or `RequestApprovalRevision` envelopes on
   `POST /api/v1/operator/commands`. A local `TestClient` probe returns `422`
   for all three because the current command and target enums omit them.
3. The current request pair is not replay-clean because commit `0942961...`
   still carries `source_commit: pending` in both PKT-006 payload bodies. The
   later pointer commit `79dc1b5` rewrites those fields to `0942961...`, but
   the advertised tuple still does not reconstruct the same machine-visible
   payload Pantheon reviewed.
4. The current UI does not enforce the screen-spec rule that a `pending`
   decision may not silently render with both `canApprove` and `canReject`
   absent. It currently falls back to a normal read-only alert instead of
   surfacing the required contract-gap condition.

No new Pantheon endpoint or client-side shadow state is authorized in this
cycle. The correct next step is Pantheon-owned BFF implementation on the
already-published contract, followed by a front-owned truthful republish that
closes the remaining replay and UI spec gaps.

## Verified UI Alignment

- `GovernanceApprovalQueue.tsx` reads the screen through
  `operatorApi.listGovernanceApprovalQueue()` and does not add raw
  component-level network calls.
- The filter rail passes `decision_type`, `risk_level`, and `decision_state`
  through to the BFF as query params. No client-side row filtering was added.
- The decision drawer renders from the selected `ApprovalQueueItem` and its
  embedded `decision_context` payload without a second fetch.
- The degradation banner keeps the queue visible while disabling approval CTAs
  when any `meta.surfaces` entry is `degraded` or `unavailable`.
- Approval, rejection, and revision target the published command envelopes on
  the existing operator command surface.
- The mirrored `frontend-feedback` machine summary exists in Pantheon at
  `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`.
- The feedback bundle is also present locally under
  `docs/pantheon-feedback/PKT-006-approval-queue/`.

## Blocking Findings

### 1. Pantheon BFF route is still missing

Published PKT-006 contract:

- `GET /api/v1/operator/governance/approval-queue`

Observed runtime result:

- `404 Not Found` from local FastAPI `TestClient`
- no PKT-006 approval-queue read handler is currently wired in
  `services/control-plane/bff/main.py`

Impact:

- the reviewed screen cannot load its primary data path against the current
  Pantheon runtime
- this is Pantheon-owned BFF work on an existing contract

### 2. Pantheon command validation still rejects the PKT-006 approval envelopes

Published PKT-006 write contract:

- `POST /api/v1/operator/commands`
- `command: "ApproveDecision"`
- `command: "RejectDecision"`
- `command: "RequestApprovalRevision"`

Observed runtime result:

- all three published envelopes return `422 Unprocessable Entity` from local
  FastAPI `TestClient`
- the current enums omit both the PKT-006 command names and the
  `ApprovalDecision` target type

Impact:

- the reviewed approval CTAs cannot complete successfully against the current
  Pantheon runtime
- Pantheon must add PKT-006 command support to the existing operator command
  surface without inventing a new route

### 3. The Git-visible transport is still not replay-clean

The current mirrored request pair advertises:

- `source_commit: 0942961a7e31bdfa5adddddf2d31d72b41b141a9`

But that commit still contains:

- `.coordination/requests/PKT-006-approval-queue-ui-done.yaml` with
  `source_commit: pending`
- `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml` with
  `source_commit: pending`

Current front history:

- commit `79dc1b5` rewrites those payloads to point at `0942961...`
- current front `HEAD` `5ddd23283dc87aee07b4b55133757a3161526158` still carries
  that rewritten form

Impact:

- replay cannot reconstruct the same machine-visible payload from the
  advertised tuple
- even after Pantheon lands the missing BFF work, the front repo still needs a
  truthful republish before PKT-006 can close

### 4. The UI still misses one published screen-spec guardrail

Published PKT-006 screen acceptance:

- a `pending` decision must not silently render with both `canApprove` and
  `canReject` absent; that condition should surface the contract gap

Observed front implementation:

- response validation checks only for the presence and boolean typing of
  `allowedActions.*`
- the drawer then renders `No approval actions available` when all three
  `allowedActions` booleans are false

Impact:

- a future backend regression on pending-decision authority would be silently
  normalized into read-only UI instead of surfacing the required contract-gap
  condition
- even after Pantheon lands the missing BFF work, one front-owned UI follow-up
  publish is still required before PKT-006 can close

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Published endpoints: unchanged
- Pantheon follow-up required:
  - implement `GET /api/v1/operator/governance/approval-queue`
  - accept `ApproveDecision`, `RejectDecision`, and
    `RequestApprovalRevision` on `POST /api/v1/operator/commands`
  - add regression coverage for the PKT-006 read and command surfaces
- Front follow-up still required after Pantheon delivery:
  - publish the canonical `frontend-feedback` and `ui-done` pair from a
    truthful Git-visible commit that fixes the replay tuple
  - keep the current PKT-006 UI files and add the pending-decision
    `canApprove`/`canReject` guardrail
- Current loop outcome: `blocked`

## Verification Performed

- Reviewed Pantheon-visible request artifacts:
  - `.coordination/requests/PKT-006-approval-queue-ui-done.yaml`
  - `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`
- Reviewed the canonical front-owned payload and feedback files in
  `../front-ai-trading-system`
- Reviewed the front implementation files:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceApprovalQueue.tsx`
  - `src/pages/governance/ApprovalDecisionDetail.tsx`
  - `src/pages/governance/types.ts`
- Verified front publication history with:
  - `git show 0942961:.coordination/requests/PKT-006-approval-queue-ui-done.yaml`
  - `git show 0942961:.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`
  - `git show 79dc1b5:.coordination/requests/PKT-006-approval-queue-ui-done.yaml`
  - `git show 79dc1b5:.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`
- Re-ran sibling front validation:
  - `npm run build`
  - Result: build passed
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient`
  and confirmed:
  - `GET /api/v1/operator/governance/approval-queue` returns `404`
  - `POST /api/v1/operator/commands` returns `422` for
    `ApproveDecision`, `RejectDecision`, and
    `RequestApprovalRevision`

## Not Completed

- No live browser QA against a running Pantheon deployment was performed in
  this review cycle
- No Pantheon implementation of the PKT-006 read route or approval commands
  was added in this front-sync pass
- No new sibling front repo publish was produced in this Pantheon-owned review
  cycle

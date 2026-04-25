# PKT-006 Governance Approval Queue Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-006-approval-queue-ui-done.yaml` against the
current PKT-006 contract, example payload, coordination replay rules, the
mirrored `frontend-feedback` request, the sibling front implementation, and the
local Pantheon BFF app.

The reviewed UI slice is aligned on the published contract:

- the screen uses the shared BFF client, keeps filters server-backed, renders
  the detail drawer from embedded `decision_context`, and disables approval
  actions when `meta.surfaces` is degraded or unavailable
- `npm run build` passed in the sibling `front-ai-trading-system` repo
- the advertised `source_commit`
  `0942961a7e31bdfa5adddddf2d31d72b41b141a9` contains the PKT-006 request
  pair, feedback bundle, and integrated UI files

Pantheon-owned backend gaps are now closed:

1. Pantheon now serves `GET /api/v1/operator/governance/approval-queue`.
2. Pantheon now accepts the published `ApproveDecision`, `RejectDecision`, and
   `RequestApprovalRevision` envelopes on `POST /api/v1/operator/commands`.
3. Targeted PKT-006 contract coverage and command executor coverage pass in the
   current workspace.

No new Pantheon endpoint or client-side shadow state is authorized in this
cycle. For the current packet scope, Pantheon's backend delivery is complete.

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

## Delivered Findings

### 1. Pantheon BFF route is live

Published PKT-006 contract:

- `GET /api/v1/operator/governance/approval-queue`

Observed runtime result:

- `200 OK` from local FastAPI `TestClient`
- PKT-006 approval-queue read handler is wired in
  `services/control-plane/bff/main.py`

Impact:

- the reviewed screen can now load its primary data path against the current
  Pantheon runtime
- this Pantheon-owned BFF gap is resolved on the existing contract

### 2. Pantheon command validation now accepts the PKT-006 approval envelopes

Published PKT-006 write contract:

- `POST /api/v1/operator/commands`
- `command: "ApproveDecision"`
- `command: "RejectDecision"`
- `command: "RequestApprovalRevision"`

Observed runtime result:

- all three published envelopes are accepted by local validation and command
  dispatch coverage
- the command and target enums now include the PKT-006 command names and the
  `ApprovalDecision` target type

Impact:

- the reviewed approval CTAs can now complete successfully against the current
  Pantheon runtime
- this Pantheon-owned command-surface gap is resolved without inventing a new
  route

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Published endpoints: live on the current workspace
- Pantheon delivery completed:
  - `GET /api/v1/operator/governance/approval-queue`
  - `ApproveDecision`, `RejectDecision`, and
    `RequestApprovalRevision` on `POST /api/v1/operator/commands`
  - regression coverage for the PKT-006 read and command surfaces
- Front follow-up required: none for the current packet scope unless a new
  replay-clean frontend return reopens the loop with fresh evidence
- Current loop outcome: `delivered` on the Pantheon backend-delivery record

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
  and confirmed the PKT-006 route and commands are wired
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt006_approval_queue_contract.py services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_command_executor.py`
  - Result: passed

## Not Completed

- No live browser QA against a running Pantheon deployment was performed in
  this review cycle
- No new sibling front repo publish was produced in this Pantheon-owned review
  cycle

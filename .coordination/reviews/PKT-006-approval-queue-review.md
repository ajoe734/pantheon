# PKT-006 Governance Approval Queue Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Findings

### 1. Pantheon does not currently serve the published `GET /api/v1/operator/governance/approval-queue` route

Published PKT-006 contract:

- `GET /api/v1/operator/governance/approval-queue`

Observed Pantheon runtime result:

- repo search finds no `approval-queue` handler in
  `services/control-plane/bff/main.py`
- loading the local FastAPI app with `TestClient(main.app)` and requesting
  `/api/v1/operator/governance/approval-queue` returns `404 Not Found`

Impact:

- the reviewed UI cannot complete an end-to-end read flow against the current
  Pantheon BFF
- this is a Pantheon-owned BFF gap on an already-published contract, not a
  front-end request for a new endpoint

### 2. Pantheon rejects all three published approval-decision command envelopes on the existing operator command surface

Published PKT-006 write contract:

- `POST /api/v1/operator/commands`
- `command: "ApproveDecision"`
- `command: "RejectDecision"`
- `command: "RequestApprovalRevision"`

Observed Pantheon runtime result:

- submitting each published envelope to `/api/v1/operator/commands` returns
  `422 Unprocessable Entity`
- `services/control-plane/bff/models.py` currently restricts `CommandType` to:
  `ApproveDeployment`, `PauseRuntime`, `ExecuteRollback`,
  `ApproveRollback`, `RejectRollback`, `ActivateKillSwitch`,
  `ApproveEvolutionDecision`, and `ExecuteEvolutionAction`
- the same model also omits `ApprovalDecision` from the allowed `target.type`
  enum

Impact:

- the reviewed Approve, Reject, and Request Revision CTAs cannot succeed
  against the current Pantheon BFF
- Pantheon must add PKT-006 command support to the existing operator command
  surface without inventing a new route or client-side shadow command

### 3. The published front payloads are still not replay-clean

Pantheon received PKT-006 `frontend-feedback` and `ui-done` payloads that both
advertise:

- `source_commit: 0942961a7e31bdfa5adddddf2d31d72b41b141a9`

The current front-repo publication head is:

- `5ddd23283dc87aee07b4b55133757a3161526158` on `main`

Verified state:

- commit `0942961a7e31bdfa5adddddf2d31d72b41b141a9` does contain the PKT-006
  request pair, feedback bundle, and UI files
- but the payload bodies at that commit still say `source_commit: pending`
- the later pointer commit `79dc1b5ecef69125fb51f1519893c2e87020864d`
  rewrites those payload bodies to point at `0942961...`, and current front
  `HEAD` `5ddd23283dc87aee07b4b55133757a3161526158` still carries that
  rewritten form

Impact:

- the advertised `payload_path + source_commit` tuple does not reconstruct the
  same machine-visible payload Pantheon reviewed
- after Pantheon lands the missing BFF support, the front repo still needs one
  truthful republish before PKT-006 can move to loop-complete

## Reviewed Artifacts

- Pantheon request mirrors:
  - `.coordination/requests/PKT-006-approval-queue-ui-done.yaml`
  - `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`
- Front-repo canonical feedback bundle in `../front-ai-trading-system`:
  - `.coordination/requests/PKT-006-approval-queue-ui-done.yaml`
  - `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-006-approval-queue/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-006-approval-queue/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-006-approval-queue/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-006-approval-queue/QA_STATUS.md`
- Contract sources:
  - `docs/bff/PKT-006-approval-queue.md`
  - `docs/screens/PKT-006-approval-queue.md`
  - `docs/examples/PKT-006-approval-queue.json`
  - `docs/pantheon-handoffs/PKT-006-approval-queue/FRONTEND_CHANGE_SPEC.md`
  - `docs/delivery-coordination-bus.md`
  - `.coordination/README.md`
- Front UI implementation under `../front-ai-trading-system`:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceApprovalQueue.tsx`
  - `src/pages/governance/ApprovalDecisionDetail.tsx`
  - `src/pages/governance/types.ts`
- Pantheon BFF entrypoint:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/models.py`
  - `services/control-plane/bff/command_executor.py`

## Verified Positives

- The current sibling implementation uses the shared BFF client only.
  `operatorApi.listGovernanceApprovalQueue()` reads
  `GET /api/v1/operator/governance/approval-queue`, and the drawer submits
  only through `operatorApi.sendCommand()`.
- Filter state round-trips to the BFF as `decision_type`, `risk_level`,
  `decision_state`, `page_token`, and `page_size`. No client-side filtering was
  added.
- Drawer content renders from the embedded `decision_context` sub-object in the
  queue payload. No secondary detail fetch was added.
- Degraded or unavailable `meta.surfaces` entries keep the queue visible while
  disabling approval CTAs in both the page and the drawer.
- The required `frontend-feedback` machine summary exists in Pantheon at
  `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`.
- The Pantheon-side feedback doc mirror was missing at review start and has now
  been restored under `docs/pantheon-feedback/PKT-006-approval-queue/` so the
  returned bundle is visible in the Pantheon repo as requested by the handoff.
- Sibling front static verification passed on the current working tree:
  - `npm run build`
  - `npx eslint src/App.tsx src/components/AppSidebar.tsx src/lib/bffClient.ts src/pages/governance/GovernanceApprovalQueue.tsx src/pages/governance/ApprovalDecisionDetail.tsx src/pages/governance/types.ts`

## Decision

`PKT-006-approval-queue` is **blocked** and is **not loop-complete**.

The current UI implementation is statically aligned on the published PKT-006
contract, but Pantheon has not implemented the contracted read and command
support yet, and the front-owned request pair is still not replay-clean. The
next step is Pantheon-owned BFF follow-up first, then a front-owned truthful
republish on the unchanged contract.

## Required Follow-up

1. Implement `GET /api/v1/operator/governance/approval-queue` in the Pantheon
   BFF without changing the published response shape or inventing shadow state.
2. Add `ApproveDecision`, `RejectDecision`, and `RequestApprovalRevision`
   support to the existing `POST /api/v1/operator/commands` surface, including
   the required `ApprovalDecision` target type.
3. Add regression coverage for:
   - approval queue read success
   - embedded `decision_context` drawer data
   - `allowedActions` gating and degraded or unavailable surfaces
   - all three PKT-006 command envelopes on the operator command surface
4. After Pantheon lands that delivery, republish the canonical front-owned
   request pair from a real Git-visible commit whose payload bodies point at a
   truthful replayable commit:
   - `.coordination/requests/PKT-006-approval-queue-frontend-feedback.yaml`
   - `.coordination/requests/PKT-006-approval-queue-ui-done.yaml`
5. Ensure the same front commit also contains:
   - `src/App.tsx`
   - `src/components/AppSidebar.tsx`
   - `src/lib/bffClient.ts`
   - `src/pages/governance/GovernanceApprovalQueue.tsx`
   - `src/pages/governance/ApprovalDecisionDetail.tsx`
   - `src/pages/governance/types.ts`
   - `docs/pantheon-feedback/PKT-006-approval-queue/`
6. Redispatch the unchanged published payloads after the replayable front
   commit exists.

## Residual Risk

- This review verified the sibling front build and targeted lint slice, but it
  did not run live browser QA against a Pantheon environment that serves the
  PKT-006 route.
- The current review did not exercise a live degraded approval-queue payload
  against a running Pantheon deployment.

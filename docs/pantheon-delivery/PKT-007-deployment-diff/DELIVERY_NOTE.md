# PKT-007 Governance Deployment Diff Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-007-deployment-diff-ui-done.yaml` against the
current PKT-007 contract, example payload, coordination replay rules, the
mirrored `frontend-feedback` request, the sibling front implementation, and the
local Pantheon BFF app.

The reviewed UI slice is statically aligned on the published contract and the
changed files validate cleanly in the sibling front repo:

- `npm run build` passed
- targeted ESLint passed for:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceDeploymentDiff.tsx`
  - `src/pages/governance/types.ts`

Pantheon-owned backend gaps are now closed:

1. Pantheon now serves `GET /api/v1/operator/deployment-diff/{plan_id}`.
2. Pantheon now accepts the published `EscalateDiff` command on
   `POST /api/v1/operator/commands`.
3. Targeted PKT-007 contract coverage and command executor coverage pass in the
   current workspace.

No new Pantheon endpoint or client-side shadow state is authorized in this
cycle. The remaining open issue for the overall loop is front-owned replay
truthfulness, not a Pantheon backend gap.

## Verified UI Alignment

- `GovernanceDeploymentDiff.tsx` reads the screen through
  `operatorApi.getDeploymentDiff()` and does not add raw component-level
  network calls.
- The diff table renders directly from backend-supplied `changes[]` entries.
  No client-side diff reconstruction from raw deployment plan fields was added.
- `first_deployment` renders an explicit no-baseline state instead of an empty
  success table.
- Approval and escalation gating render from `allowedActions` only.
- The approval CTA remains disabled when
  `meta.surfaces.deployment_diff = unavailable`.
- Escalation targets the published `EscalateDiff` envelope on the existing
  operator command surface.
- The mirrored `frontend-feedback` machine summary exists in Pantheon at
  `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`.
  Per `.coordination/README.md`, the feedback docs themselves remain canonical
  in the front repo and are consumed by path rather than mirrored into
  Pantheon.

## Delivered Findings

### 1. Pantheon BFF route is live

Published PKT-007 contract:

- `GET /api/v1/operator/deployment-diff/{plan_id}`

Observed runtime result:

- `200 OK` from local FastAPI `TestClient`
- PKT-007 deployment diff read handler is now wired in
  `services/control-plane/bff/main.py`

Impact:

- the reviewed screen cannot load its primary data path against the current
  Pantheon runtime
- this is Pantheon-owned BFF work on an existing contract

### 2. Pantheon command validation now accepts `EscalateDiff`

Published PKT-007 write contract:

- `POST /api/v1/operator/commands`
- `command: "EscalateDiff"`

Observed runtime result:

- the published `EscalateDiff` envelope is accepted by local validation and
  command dispatch coverage
- the command vocabulary now includes `EscalateDiff`

Impact:

- the reviewed escalation CTA can now complete successfully against the current
  Pantheon runtime
- this Pantheon-owned command-surface gap is resolved without inventing a new
  route

### 3. The GitHub-visible transport is still not replay-clean

The current sibling request pair advertises:

- `source_commit: 87340e96ce4247ccc177e8dff7579e804991b895`

But that commit still omits:

- `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`
- `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
- `docs/pantheon-feedback/PKT-007-deployment-diff/`
- `src/pages/governance/GovernanceDeploymentDiff.tsx`

The sibling working tree still shows those artifacts as unpublished local
changes at front HEAD `87340e96ce4247ccc177e8dff7579e804991b895`.

Impact:

- replay cannot reconstruct the reviewed UI cycle from the advertised front
  commit
- even after Pantheon lands the missing BFF work, the front repo still needs a
  truthful republish before PKT-007 can close

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Published endpoints: live on the current workspace
- Pantheon delivery completed:
  - `GET /api/v1/operator/deployment-diff/{plan_id}`
  - `EscalateDiff` on `POST /api/v1/operator/commands`
  - regression coverage for the PKT-007 read and command surfaces
- Front follow-up still required after Pantheon delivery:
  - publish the canonical `frontend-feedback` and `ui-done` pair from a
    truthful Git-visible commit that also contains the PKT-007 feedback bundle
    and UI files
- Current loop outcome: `delivered` on the Pantheon backend-delivery record;
  packet loop remains open in the front-owned follow-up cycle

## Verification Performed

- Reviewed Pantheon-visible request artifacts:
  - `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`
  - `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
- Reviewed the canonical front-owned payload and feedback files in
  `../front-ai-trading-system`
- Reviewed the front implementation files:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceDeploymentDiff.tsx`
  - `src/pages/governance/types.ts`
- Re-ran sibling front validation:
  - `npm run build`
  - `npx eslint src/App.tsx src/components/AppSidebar.tsx src/lib/bffClient.ts src/pages/governance/GovernanceDeploymentDiff.tsx src/pages/governance/types.ts`
  - Result: build passed, targeted ESLint passed
- Probed the current Pantheon BFF app by loading
  `services/control-plane/bff/main.py` with FastAPI `TestClient`
  and confirmed the PKT-007 route and command are wired
- Re-ran targeted Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt007_deployment_diff_contract.py services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_command_executor.py`
  - Result: passed

## Not Completed

- No live browser QA against a running Pantheon deployment was performed in
  this review cycle
- No new sibling front repo replay commit or refreshed request pair was
  produced in this Pantheon-owned review cycle

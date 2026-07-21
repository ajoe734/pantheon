# PKT-007 Governance Deployment Diff Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Findings

### 1. Pantheon does not yet serve the published deployment diff read route

Published PKT-007 contract:

- `GET /api/v1/operator/deployment-diff/{plan_id}`

Observed Pantheon runtime result:

- Loading `services/control-plane/bff/main.py` with FastAPI `TestClient` and
  requesting `/api/v1/operator/deployment-diff/plan-dp-001` returns
  `404 Not Found`
- `rg -n "deployment-diff" services/control-plane/bff/main.py` finds no PKT-007
  read handler in the current BFF app

Impact:

- the reviewed UI cannot complete an end-to-end read flow against Pantheon
- this is a Pantheon-owned BFF gap on an already-published contract, not a new
  endpoint request from the front end

### 2. Pantheon rejects the published `EscalateDiff` command on the existing operator command surface

Published PKT-007 write contract:

- `POST /api/v1/operator/commands`
- `command: "EscalateDiff"`
- `action: "escalate_diff"`

Observed Pantheon runtime result:

- submitting the published PKT-007 command envelope to
  `/api/v1/operator/commands` returns `422 Unprocessable Entity`
- the validation error enumerates only:
  `ApproveDeployment`, `PauseRuntime`, `ExecuteRollback`,
  `ActivateKillSwitch`, `ApproveEvolutionDecision`, and
  `ExecuteEvolutionAction`

Impact:

- the reviewed escalation CTA cannot succeed against the current Pantheon BFF
- the correct follow-up is to add `EscalateDiff` support to the existing
  command surface without inventing a new route or client-side shadow command

### 3. The advertised `source_commit` is not replayable and does not contain the claimed PKT-007 publication set

Pantheon received PKT-007 `frontend-feedback` and `ui-done` payloads that both
advertise:

- `source_commit: 87340e96ce4247ccc177e8dff7579e804991b895`

But that front-repo commit does not contain the claimed request pair, feedback
bundle, or the new Deployment Diff page file:

- `git -C ../front-ai-trading-system show 87340e96ce4247ccc177e8dff7579e804991b895:.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`
  fails because the payload path is absent from that commit
- `git -C ../front-ai-trading-system show 87340e96ce4247ccc177e8dff7579e804991b895:.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
  fails for the same reason
- `git -C ../front-ai-trading-system ls-tree -r --name-only 87340e96ce4247ccc177e8dff7579e804991b895 -- src/App.tsx src/components/AppSidebar.tsx src/lib/bffClient.ts src/pages/governance/GovernanceDeploymentDiff.tsx src/pages/governance/types.ts docs/pantheon-feedback/PKT-007-deployment-diff .coordination/requests/PKT-007-deployment-diff-ui-done.yaml .coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
  returns only `src/App.tsx`, `src/components/AppSidebar.tsx`,
  `src/lib/bffClient.ts`, and `src/pages/governance/types.ts`
- the current sibling front HEAD is the same
  `87340e96ce4247ccc177e8dff7579e804991b895`, and
  `git -C ../front-ai-trading-system status --short` still shows
  `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`,
  `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`,
  `docs/pantheon-feedback/PKT-007-deployment-diff/`, and
  `src/pages/governance/GovernanceDeploymentDiff.tsx` as unpublished local
  changes

Impact:

- the GitHub-visible `payload_path + source_commit` tuple is broken
- supervisor replay cannot reconstruct the reviewed UI cycle from the advertised
  front commit
- even after Pantheon lands the missing BFF support, the front repo still needs
  one truthful republish before PKT-007 can move to loop-complete

## Reviewed Artifacts

- Pantheon request mirrors:
  - `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`
  - `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
- Front-repo canonical feedback bundle in `../front-ai-trading-system`:
  - `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`
  - `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-007-deployment-diff/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-007-deployment-diff/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-007-deployment-diff/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-007-deployment-diff/QA_STATUS.md`
- Contract sources:
  - `docs/bff/PKT-007-deployment-diff.md`
  - `docs/screens/PKT-007-deployment-diff.md`
  - `docs/examples/PKT-007-deployment-diff.json`
  - `docs/pantheon-handoffs/PKT-007-deployment-diff/FRONTEND_CHANGE_SPEC.md`
  - `docs/bff/PKT-005-degradation-banner.md`
  - `.coordination/README.md`
- Front UI implementation under `../front-ai-trading-system`:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceDeploymentDiff.tsx`
  - `src/pages/governance/types.ts`
- Pantheon BFF entrypoint:
  - `services/control-plane/bff/main.py`

## Verified Positives

- The current sibling implementation uses the shared BFF client only.
  `operatorApi.getDeploymentDiff()` reads
  `GET /api/v1/operator/deployment-diff/{plan_id}`, and
  `operatorApi.escalateDeploymentDiff()` targets the published
  `POST /api/v1/operator/commands` surface.
- The diff table renders directly from backend-supplied `changes[]` entries.
  No client-side reconstruction from raw plan fields was added.
- `first_deployment` is rendered as an explicit no-baseline state instead of an
  empty success table.
- Approval and escalation gating are driven from `allowedActions`, and the
  approval route is disabled when
  `meta.surfaces.deployment_diff = unavailable`.
- The paired `frontend-feedback` machine summary has been mirrored into
  Pantheon as a request copy at
  `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`.
  Per `.coordination/README.md`, the feedback docs themselves remain canonical
  in the front repo and are consumed by path rather than duplicated into
  Pantheon.
- Sibling front static verification passed in the current working tree:
  - `npm run build`
  - `npx eslint src/App.tsx src/components/AppSidebar.tsx src/lib/bffClient.ts src/pages/governance/GovernanceDeploymentDiff.tsx src/pages/governance/types.ts`

## Decision

`PKT-007-deployment-diff` is **blocked** and is **not loop-complete**.

The current UI implementation is statically aligned on the published PKT-007
contract, but Pantheon has not implemented the contracted read and command
support yet, and the returned front-repo cycle is not replay-clean. The next
step is Pantheon-owned BFF follow-up first, then a front-owned truthful
republish on the unchanged contract.

## Required Follow-up

1. Implement `GET /api/v1/operator/deployment-diff/{plan_id}` in the Pantheon
   BFF without changing the published PKT-007 response shape or inventing
   shadow state.
2. Add `EscalateDiff` support to the existing
   `POST /api/v1/operator/commands` surface while keeping the published command
   envelope stable.
3. Add regression coverage for:
   - deployment diff read success
   - `first_deployment` no-baseline behavior
   - `allowedActions` gating and degraded or unavailable surfaces
   - `EscalateDiff` command acceptance on the operator command surface
4. After Pantheon lands that delivery, republish the canonical front-owned
   request pair from a real Git-visible commit:
   - `.coordination/requests/PKT-007-deployment-diff-frontend-feedback.yaml`
   - `.coordination/requests/PKT-007-deployment-diff-ui-done.yaml`
5. Ensure that same front commit also contains:
   - `src/App.tsx`
   - `src/components/AppSidebar.tsx`
   - `src/lib/bffClient.ts`
   - `src/pages/governance/GovernanceDeploymentDiff.tsx`
   - `src/pages/governance/types.ts`
   - `docs/pantheon-feedback/PKT-007-deployment-diff/`
6. Redispatch the unchanged published payloads after the replayable front
   commit exists.

## Residual Risk

- This review verified the sibling front build and targeted lint slice, but it
  did not run live browser QA against a Pantheon environment that serves the
  PKT-007 route.
- `docs/screens/PKT-007-deployment-diff.md` inherits PKT-005 degradation
  semantics. The current review did not exercise a live `meta.staleness`
  payload against the screen.

## 2026-04-19 Closeout Addendum

Pantheon re-verified PKT-007 after the replay-clean front publication.

- The front repo now publishes the canonical request pair at
  `c9c1e20726bfc1d35f3ddcbb4f7552859f1d8f5d`.
- Both request bodies now point `source_commit` at
  `77ab876e05dbb206f4fd4abc39051df86f6127c2`, whose tree contains the
  governance deployment diff screen, request pair, and feedback bundle.
- Pantheon's deployment-diff contract and degraded/unavailable shapes remain
  green:
  `python3 -m pytest services/control-plane/bff/test_pkt007_deployment_diff_contract.py -q`
  completed with `3 passed`.

## Final Decision

**APPROVED.**

# PKT-004 Deployment / Approval Drilldowns Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Findings

### 1. No blocking front-end findings

The returned PKT-004 deployment and approval drilldown UI is replayable from
`source_commit 0e93994eddfc8651e696191964c31c65c56c6201`, matches the
published read-only contract, and passed the front build, targeted feature-file
ESLint, Pantheon regression, and live local BFF acceptance checks completed in
this review cycle.

### 2. Non-blocking feedback-bundle metadata drift

The committed feedback bundle prose still references working-tree baseline
`8d23e02dceb690ed35c1b3800749d2ca90ae4369` inside the sibling front repo.
This does not block approval because the authoritative machine-readable request
pair correctly points at `0e93994eddfc8651e696191964c31c65c56c6201`, but future
bundles should keep the embedded prose aligned with the published
`source_commit`.

### 3. Pantheon-side acceptance drift found and resolved during review

The first seeded live-BFF probe exposed a Pantheon-owned local acceptance gap:
fallback deployment-plan snapshots were returning `id` without the required
`plan_id` field. Pantheon fixed that in
`services/control-plane/bff/read_store.py` and extended the regression in
`services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py`
at backend commit `287a541774a3431522f815827c8dcf5ce7e71a4b`. The rerun live
probe then passed.

## Scope

Review the returned `PKT-004-deployment-approval-drilldowns` `ui-done` and
`frontend-feedback` handoffs against the published Pantheon contract, example
payload, replay rules, and the current Pantheon BFF behavior.

## Reviewed Artifacts

- Front-repo request payloads:
  - `../front-ai-trading-system/.coordination/requests/PKT-004-deployment-approval-drilldowns-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-004-deployment-approval-drilldowns-frontend-feedback.yaml`
- Front-repo feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-deployment-approval-drilldowns/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-deployment-approval-drilldowns/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-deployment-approval-drilldowns/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-deployment-approval-drilldowns/QA_STATUS.md`
- Contract sources:
  - `.coordination/responses/PKT-004-deployment-approval-drilldowns-contract-ready.yaml`
  - `docs/bff/PKT-004-deployment-approval-drilldowns.md`
  - `docs/examples/PKT-004-deployment-approval-drilldowns.json`
  - `docs/screens/PKT-004-deployment-approval-drilldowns.md`
  - `docs/pantheon-handoffs/PKT-004-deployment-approval-drilldowns/FRONTEND_CHANGE_SPEC.md`
- Front implementation files:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/persona/types.ts`
  - `../front-ai-trading-system/src/pages/persona/DeploymentPlanList.tsx`
  - `../front-ai-trading-system/src/pages/persona/DeploymentPlanDetail.tsx`
  - `../front-ai-trading-system/src/pages/persona/ApprovalDecisionList.tsx`
  - `../front-ai-trading-system/src/pages/persona/ApprovalDecisionDetail.tsx`
  - `../front-ai-trading-system/src/pages/persona/PersonaDetail.tsx`
  - `../front-ai-trading-system/src/pages/persona/BindingDetail.tsx`

## Verification Performed

- Verified the current request pair at
  `28b0bb8b281b5969af4be83850be525868ca11b3` advertises:
  - `source_commit: 0e93994eddfc8651e696191964c31c65c56c6201`
- Verified commit `0e93994eddfc8651e696191964c31c65c56c6201`
  contains the nine UI files listed in `changed_files` plus the PKT-004
  feedback bundle directory
- Reviewed route wiring in `src/App.tsx`:
  - `/personas/deployment-plans`
  - `/personas/deployment-plans/:planId`
  - `/personas/approval-decisions`
  - `/personas/approval-decisions/:decisionId`
- Confirmed the list pages forward Pantheon-owned query params instead of
  filtering rows locally:
  - `status`, `capital_pool_id`
  - `outcome`, `state`
- Confirmed the detail pages remain read-only and cross-link into PKT-001
  governance surfaces instead of inventing new command payloads or shadow
  state
- Ran sibling front repo validation:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/persona/types.ts src/pages/persona/DeploymentPlanList.tsx src/pages/persona/DeploymentPlanDetail.tsx src/pages/persona/ApprovalDecisionList.tsx src/pages/persona/ApprovalDecisionDetail.tsx src/pages/persona/PersonaDetail.tsx src/pages/persona/BindingDetail.tsx`
  - Result: passed
- Ran Pantheon verification:
  - `python3 -m pytest services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py services/control-plane/bff/test_w4_remaining_catalog.py -q`
  - Result: passed
- Ran a socket-level HTTP smoke against a real local Uvicorn-served Pantheon
  BFF bound to a seeded `ReadSurfaceStore` and verified:
  - `GET /api/v1/deployment-plans?status=approved&capital_pool_id=pool-main` ->
    `200`, `meta.total = 1`
  - `GET /api/v1/deployment-plans?status=rejected&capital_pool_id=pool-main` ->
    `200`, `meta.total = 0`
  - `GET /api/v1/deployment-plans/plan-F-042` -> `200`
  - `GET /api/v1/approval-decisions?outcome=approved&state=decided` -> `200`,
    `meta.total = 1`
  - `GET /api/v1/approval-decisions?outcome=approved&state=pending` -> `200`,
    `meta.total = 0`
  - `GET /api/v1/approval-decisions/approval-042` -> `200`
  - viewer access to `GET /api/v1/deployment-plans` ->
    `403 INSUFFICIENT_ROLE`
  - missing auth on `GET /api/v1/deployment-plans` -> `401 INVALID_TOKEN`

## Decision

`PKT-004-deployment-approval-drilldowns` is **approved**.

No additional front-end or Pantheon follow-up is required for the currently
published scope.

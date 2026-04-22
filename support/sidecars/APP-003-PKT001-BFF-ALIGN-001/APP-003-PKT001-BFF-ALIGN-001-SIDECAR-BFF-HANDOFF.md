# APP-003-PKT001-BFF-ALIGN-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-003-PKT001-BFF-ALIGN-001` - align PKT-001 deployment-review with the published operator list contract  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Claude2`  
**Parent Status**: `done`  
**Sidecar Task**: `APP-003-PKT001-BFF-ALIGN-001-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-22`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 truth, canonical BFF
> contracts, runtime behavior, registry/governance implementations, or the
> parent execution record. It packages the current PKT-001 route truth, the
> already-published frontend handoff materials, and the remaining front-owned
> publication replay residual so the parent review can stay narrowly scoped.

## 1. Executive Summary

`APP-003-PKT001-BFF-ALIGN-001` was opened to close one real Pantheon-owned
gap: the contract promised `GET /api/v1/operator/deployment-plans`, while the
workspace only had the non-operator list route plus the deployment-review
detail route.

Current repo truth is now aligned:

- `GET /api/v1/operator/deployment-plans` is live in
  `services/control-plane/bff/main.py`.
- `GET /api/v1/operator/deployment-review/{plan_id}` remains the detail
  snapshot authority and already returns the full
  `allowedActions.canApprove/canReject/canPromoteToPaper` shape.
- runtime event streaming is now recorded truthfully as the existing
  `PKT-005` cross-cut substrate at
  `GET /api/v1/runtime/{runtime_id}/events/stream`, not as a missing PKT-001
  route.
- the dedicated PKT-001 contract test passes in the current workspace.
- the module-local frontend handoff bundle for PKT-001 already exists and is
  still the correct consumer-facing packet.

The only remaining blocker described by the latest PKT-001 frontend feedback is
not a Pantheon BFF gap. It is front-owned publication truth:

- the front repo must republish the `ui-done` and `frontend-feedback` request
  pair from one Git-visible commit that actually contains both request files
  plus the updated feedback bundle
- that republished bundle must keep the runtime SSE dependency explicit as an
  inherited `PKT-005` substrate, not as a new PKT-001 snapshot endpoint

Reviewer-safe conclusion:

- do not reopen PKT-001 backend alignment work
- do keep the parent task framed as backend-aligned with an external
  coordination residual

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | durable lifecycle truth: parent task is in `review`; sidecar is support-only |
| `.orchestrator/task-briefs/app_003_pkt001_bff_align_001_sidecar_bff_handoff.md` | scoped execution brief and artifact target |
| `docs/reviews/2026-04-22-pkt001-deployment-review-blocker-execution-packet.md` | original execution packet that materialized the missing operator list-route gap |
| `docs/bff/PKT-001-deployment-review-console.md` | canonical PKT-001 list/detail contract and the approved PKT-005 SSE boundary text |
| `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md` | existing frontend handoff bundle for the deployment-review screen |
| `.coordination/responses/PKT-001-deployment-review-backend-delivery.yaml` | backend-delivery record stating Pantheon runtime/contract work is complete in this workspace |
| `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml` | latest Pantheon review of the returned frontend bundle; records the remaining front-owned replay residual |
| `docs/pantheon-feedback/PKT-001-deployment-review/LOVABLE_CHANGE_FEEDBACK.md` | human-readable frontend review summary aligned with the PKT-005 cross-cut decision |
| `docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json` | latest API-gap record now says `no_open_gaps` |
| `services/control-plane/bff/main.py` | live operator list route and detail route implementation |
| `services/control-plane/bff/test_pkt001_deployment_review_console_contract.py` | dedicated regression proof for list/detail route behavior |
| `docs/examples/PKT-001-deployment-review-console.json` | frontend example payload for the published screen contract |

## 3. PKT-001 Query Gap And Current Repo State

### 3.1 What the parent task needed to fix

The execution packet identified two Pantheon-owned ambiguities:

- the published contract exposed `GET /api/v1/operator/deployment-plans`, but
  the workspace still returned `404` at that operator-scoped list path
- frontend closeout wording still mixed one real route gap with one boundary
  question about whether runtime SSE belonged to PKT-001 or to the shared
  `PKT-005` substrate

### 3.2 Current route truth after alignment

The current workspace now exposes the exact surface family the frontend packet
expects:

- `GET /api/v1/operator/deployment-plans`
- `GET /api/v1/operator/deployment-review/{plan_id}`
- `POST /api/v1/operator/commands`

Important backend-owned semantics now locked in by contract plus test:

- list rows are page-shaped and include `plan_id`, `artifact_id`,
  `target_stage`, `risk_level`, `governance_outcome`, and `submitted_at`
- list responses carry `page_info.next_page_token`,
  `meta.snapshot_at`, and `meta.surfaces`
- detail responses carry the composed deployment-review snapshot plus the full
  `allowedActions` shape
- degraded or unavailable read surfaces are surfaced through `meta.surfaces`
  and `meta.degradation`; CTA disablement remains backend-owned
- runtime SSE is incremental-only decoration after `runtime_binding.id` is
  known; snapshot truth for list rows, detail content, degradation banners, and
  CTA authority remains owned by PKT-001 snapshot responses

### 3.3 Current gap classification

| Item | State | Notes |
|---|---|---|
| Operator deployment-plan list route | closed | mounted in `services/control-plane/bff/main.py` and covered by the dedicated PKT-001 contract test |
| Deployment-review detail route | already closed | detail route remained live; parent task only had to keep its contract wording aligned |
| `allowedActions` detail shape | closed | test asserts the full `canApprove/canReject/canPromoteToPaper` shape |
| PKT-001 vs PKT-005 SSE boundary | closed | latest contract, backend-delivery, and frontend-feedback records all say runtime SSE stays in `PKT-005` |
| PKT-001 module-local frontend handoff bundle | already published | `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md` remains valid |
| Open Pantheon-side API gap | none | `docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json` now says `no_open_gaps` |
| Front repo publication replay truth | open | latest frontend-feedback requires republishing the request pair and updated feedback bundle from one truthful commit |

## 4. Verification Replayed For This Sidecar

On `2026-04-22`, this sidecar re-verified the most relevant PKT-001 evidence:

- `pytest -q services/control-plane/bff/test_pkt001_deployment_review_console_contract.py`
- result: `3 passed`
- `python3 -m json.tool docs/examples/PKT-001-deployment-review-console.json`
- result: parses cleanly

This sidecar did not rerun unrelated frontend build steps, browser QA, or other
module contracts because the task scope is support-only handoff packaging.

## 5. Frontend Handoff Boundary

### 5.1 Existing packet set the frontend should continue to use

The parent lane does not need a new frontend spec. The existing handoff bundle
is already the right dispatch surface:

- `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md`
- `.coordination/responses/PKT-001-deployment-review-contract-ready.yaml`
- `.coordination/responses/PKT-001-deployment-review-lovable-ui-task.yaml`
- `.coordination/responses/PKT-001-deployment-review-backend-delivery.yaml`
- `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`

What changed is not the UI packet shape. What changed is the truthful
classification of the residual:

- Pantheon BFF alignment is complete in the current workspace
- the remaining work lives in front-repo replay/publication hygiene

### 5.2 Frontend rules that remain non-negotiable

Frontend consumers should keep following the published handoff rules:

- fetch list state from `GET /api/v1/operator/deployment-plans`
- fetch detail state from `GET /api/v1/operator/deployment-review/{plan_id}`
- drive CTA visibility only from backend-shaped `allowedActions`
- drive degradation banners and CTA disablement from `meta.surfaces` /
  `meta.degradation`
- pass filters to the BFF as query params; do not filter or sort client-side
- if runtime live decoration is desired, open
  `GET /api/v1/runtime/{runtime_id}/events/stream` only through the shared
  `PKT-005` substrate after the detail snapshot reveals `runtime_binding.id`
- do not treat SSE as a replacement for PKT-001 snapshots

## 6. Truthful Operator And Frontend Journey

The reviewer can evaluate the aligned PKT-001 journey in this order:

1. Operator opens the deployment-review console and requests
   `GET /api/v1/operator/deployment-plans?status=...&page_size=...`.
2. The BFF returns page-shaped rows plus `page_info`, `meta.snapshot_at`, and
   `meta.surfaces`. The frontend renders exactly those rows and does not derive
   its own queue state.
3. Operator selects one plan. The frontend requests
   `GET /api/v1/operator/deployment-review/{plan_id}` and renders the composed
   detail payload plus backend-shaped `allowedActions`.
4. If `runtime_binding.id` is present, the screen may optionally decorate the
   detail panel with runtime events through the shared
   `PKT-005` SSE substrate. This is read-only enhancement, not a new PKT-001
   authority path.
5. Approve or reject actions submit `POST /api/v1/operator/commands` with the
   documented `ApproveDeployment` payload and required `audit_context.reason`.
6. If any required surface is degraded or unavailable, the screen shows the
   degradation banner and disables all affected CTAs instead of inventing
   fallback authority.

## 7. Remaining Front-Owned Residuals

These items remain open, but they do not justify reopening Pantheon BFF work:

### RESIDUAL-PKT001-001 — request-pair replay truth

Latest frontend-feedback says the advertised front `source_commit`
`faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7` still does not replay the request
pair truthfully. The front repo must republish:

- `.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
- `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml`

from one Git-visible commit that actually contains those request files and the
updated feedback bundle.

### RESIDUAL-PKT001-002 — feedback bundle publication must preserve the PKT-005 decision

The republished front bundle must keep these facts explicit:

- runtime SSE is inherited from `PKT-005`
- no new PKT-001 snapshot endpoint is being requested
- `docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json`
  should remain a no-open-gaps record

### RESIDUAL-PKT001-003 — live browser QA is still non-blocking follow-up

The latest frontend-feedback also keeps live browser QA against a running
Pantheon BFF as a non-blocking follow-up. That is runtime validation, not a
reason to mark PKT-001 backend alignment unfinished.

## 8. Reviewer Checklist

For `Claude` reviewing this sidecar:

- confirm the packet stays support-only and does not redefine canonical truth
- confirm it classifies the operator list-route gap as closed in the current
  workspace
- confirm it preserves the runtime SSE boundary as a `PKT-005` cross-cut rather
  than widening PKT-001
- confirm it points frontend consumers back to the already-published PKT-001
  handoff bundle instead of inventing a second packet family
- confirm it isolates the remaining blocker as front-owned publication replay
  truth, not Pantheon backend incompleteness

## 9. Suggested Parent-Task Interpretation

If the parent review accepts the current PKT-001 evidence, the safe closeout
framing is:

- Pantheon-owned BFF alignment for PKT-001 is complete in this workspace
- the current residual is external/front-owned publication truth
- future work, if any, should be phrased as a coordination replay or frontend
  redispatch slice rather than another backend route-alignment task

## 10. Sidecar Scope Check

| Check | Result |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched | PASS |
| No runtime / registry / governance implementation edits | PASS |
| Focus stays on BFF/frontend handoff boundary | PASS |
| Parent owner keeps absorption discretion | PASS |

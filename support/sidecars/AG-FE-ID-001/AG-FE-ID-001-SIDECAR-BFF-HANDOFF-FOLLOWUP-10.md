# AG-FE-ID-001 Followup-10 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-20` |
| Status | `review approved; owner closeout prepared` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, OpenClaw adapter code, or execute-plans source.

## 1. Purpose

This tenth followup updates the AG-FE-ID-001 BFF/frontend handoff after the
branch was fast-forwarded to `origin/dev` at merge commit
`ac0d55c1bffdd5791c529cc915ca531e08c2c8d2`.

The important delta from FOLLOWUP-9 is that the backend servant ensure path is
no longer a 501-only stub:

1. `AG-BE-ID-002` is archived `done`. `POST /bff/agora/servant/ensure` now
   creates or reconciles the user-private Agora servant persona, syncs the
   OpenClaw servant agent, requires `Idempotency-Key` and `X-Request-Id`, and
   returns a `ServantProfile` envelope.
2. `AG-BE-ID-003` is active but `blocked`. The servant session facade is still
   unavailable because `ServantSessionCreateRequest` lacks the `session_type`
   needed for interactive/trainer/research_task routing.
3. `execute-plans/src/lib/bff-v1/agora/types.ts` in this checkout now mirrors
   Agora contract version `1.1` with `17` schemas and `96` operations, including
   servant, workshop, and dashboard v2 operations.
4. The parent frontend target files remain missing: `AgoraApp.tsx`,
   `identity.ts`, and `servant.ts` do not exist in this checkout.
5. `execute-plans/src/entries/agora-main.tsx` still renders `AskPersonas`
   directly. The parent still needs a shell gate before exposing ask/session
   behavior.

One contract/runtime detail remains important for the parent: v1.1 OpenAPI
marks `/servant/ensure` request body as required and lists `201` for new
provisioning, while current runtime/tests post no body and receive `200` for
both create and reconcile. The parent client should be written against the
observed runtime while flagging this as a contract reconciliation item, not
silently assuming generated operation semantics are exact.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | `in_progress` | This packet is the only intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | archived `done`; PR #1853 merged | Previous packet is superseded only where this packet records newer backend/type-mirror state. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in durable task state. Because `AG-BE-ID-003` is blocked, session success remains unavailable. |
| `AG-FE-000` | archived `done` | Separate Agora/Management entry/build/audience baseline exists. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI/capability contract is merged and closed. |
| `AG-XR-002` | archived `done` | Original generated type/drift baseline is closed. Current checkout has later v1.1 type mirror updates. |
| `AG-BE-ID-001` | archived `done` | `/bff/agora/me`, `/bff/agora/capabilities`, and fail-closed user-private scope are available. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` success path is implemented and verified. |
| `AG-BE-ID-003` | `blocked`; waiting for `Claude` | `/bff/agora/servant/sessions*` runtime facade remains unavailable pending `session_type` contract disposition. |
| `AG-XR-003` | `blocked`; waiting for `Claude2` | Compatibility manifest/checksum gate is blocked by broader release gate/type-generation disposition. Do not use it as deployment proof. |
| `AG-FE-DB-001` | archived `done` | Widget registry/renderer work exists, but it is dashboard scope, not servant/session readiness. |
| `AG-FE-DB-003` | archived `done` | Widget revision drawer exists, still dashboard scope. |
| `AG-FE-DB-004` | `review_approved` | Dashboard proposal/change-log work is pending owner closeout; not a parent ID shell unblock. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The servant profile can now be ensured, but the interactive,
trainer, and research session facade cannot be claimed complete.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_10.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Confirms owner, reviewer, artifact, and in-progress state. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent is still `todo` and depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade is currently blocked on `session_type` contract disposition. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md` | Previous approved handoff baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9-REVIEW.md` | Previous review approval record. |
| `services/control-plane/bff/agora/router.py` | Runtime implements `/bff/agora/me` and `/bff/agora/capabilities`. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime implements successful `/bff/agora/servant/ensure`. |
| `services/control-plane/bff/tests/test_agora_router.py` | Current focused tests prove servant ensure create/reconcile/header behavior. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 servant/session/workshop/dashboard route artifact. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Current generated Agora v1.1 snapshot in this checkout. |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing ask UI still uses legacy `@/lib/bff/agora`. |
| `execute-plans/src/lib/bff-v1/agora/dashboard.ts` | Dashboard helper exists, but uses direct fetch and is not an ID/servant client. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-9

| Change | What changed | Parent implication |
|---|---|---|
| Branch currentness | This worktree was fast-forwarded from `d32fb4ad` to `origin/dev` at `ac0d55c1`. | The packet reflects merged state through PR #1863. |
| `AG-BE-ID-002` closed | Archived `done`; closeout says implementation PR #1855 and closeout PR #1859 merged. | Parent can now call `POST /bff/agora/servant/ensure` as a real backend path, not a 501 stub. |
| Servant ensure runtime | `services/control-plane/bff/agora/servant/router.py` now creates or reconciles exactly one user-private servant persona and syncs OpenClaw. | `servant.ts` should handle 200 success and 503 OpenClaw dependency failure; it should no longer hard-code 501 as the expected current state. |
| Required servant headers | Runtime requires `Idempotency-Key` and `X-Request-Id`; missing `Idempotency-Key` is tested as 422. | Parent servant client must generate or receive these headers for ensure calls. |
| Servant ensure contract/runtime mismatch | v1.1 OpenAPI declares a required `ServantEnsureRequest` body and `201` for new provisioning; current runtime tests post no body and assert `200` for create/reconcile. | Parent should accept the current runtime response while recording the mismatch for backend/contract follow-up. |
| `AG-BE-ID-003` blocked | Task is blocked because the v1.1 request schema has `intent`, `strategy_ref`, and `metadata`, but no `session_type`; C1 common envelope lists `session_type`. | Parent must keep ask/session/command surfaces disabled or read-only until reviewer resolves how session type is supplied or derived. |
| Type mirror current in checkout | `types.ts` now reports `contract_version: "1.1"`, `operation_count: 96`, and includes `ensureAgoraServant` plus `createServantSession`. | Parent can cite local generated type mirror for operation inventory. It still needs concrete `identity.ts` and `servant.ts` client wrappers. |
| Frontend target files | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing. | Parent cannot close until these files or an approved equivalent are implemented and tested. |
| Agora entry | `agora-main.tsx` still imports and renders `AskPersonas`. | Parent should replace this with an Agora status shell gate before exposing ask/session behavior. |
| Dashboard stream advanced | Widget renderer/revision and dashboard proposal UI work landed or reached review-approved. | Dashboard readiness must stay separate from identity/servant/session shell readiness. |

## 5. BFF Query Ledger For Parent

| Route | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy. | Still not listed as an operation in `agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, or `execute-plans/src/lib/bff-v1/agora/types.ts`. | Parent may use it as accepted interim runtime route truth for identity readiness. Keep the client narrow and document that it is runtime-only, not generated operation coverage. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Parent may use it for readiness/capability display. Do not claim generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; creates or reconciles a user-private servant and syncs OpenClaw. Current tests post no body and receive `200` for both create and reconcile. | Present in `agora_v1_1.openapi.yaml` and generated `types.ts` as `ensureAgoraServant`; OpenAPI declares required `ServantEnsureRequest` body and `201` for new provisioning. | `servant.ts` should post with `Idempotency-Key` and `X-Request-Id`, parse current 200 `ServantProfile`, and handle 401/403/422/503 explicitly. It should record the body/status mismatch instead of assuming generated semantics are exact. |
| `GET /bff/agora/servant` | No runtime handler found in the current router; only `/ensure` is implemented under the servant sub-router. | Present in v1.1 OpenAPI and generated `types.ts` as `getAgoraServant`. | Do not build parent shell logic that depends on this read route until runtime implementation lands. Use `/ensure` for the current servant status proof if parent scope accepts that side effect. |
| `POST /bff/agora/servant/reconcile` | No runtime handler found in the current servant sub-router. | Present in v1.1 OpenAPI and generated `types.ts`. | Keep out of parent UI until runtime support exists. |
| `POST /bff/agora/servant/sessions` and session detail/message/terminate/stream routes | Not implemented in the current servant sub-router. `AG-BE-ID-003` is blocked before implementation. | Present in v1.1 OpenAPI and generated `types.ts`, but `ServantSessionCreateRequest` lacks `session_type`. | Keep Ask/session/command-line surfaces disabled or read-only. Do not route `AskPersonas` through servant sessions yet. |
| Dashboard recipe/widget routes | Runtime/dashboard frontend work has advanced separately. | Present in v1.1 OpenAPI and generated `types.ts`. | Useful for dashboard tasks only. Do not use dashboard readiness to unlock servant/session UI controls. |

The safe BFF facts today are: user-private identity scope, filtered capability
readiness, successful servant ensure/provision/reconcile through `/ensure`, and
explicit absence of the servant session facade.

## 6. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Exists and renders `AskPersonas` directly. | Parent should route through `AgoraApp.tsx` or an approved equivalent status shell before exposing ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | **MISSING**. | Parent must add the shell or block for missing design/spec authority. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | **MISSING**. | Parent should add narrow clients for `/me` and `/capabilities`; these are runtime routes, not generated operations. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | **MISSING**. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present; generated v1.1 snapshot with 17 schemas and 96 operations. | Reuse `ServantProfile`, operation inventory, and compatibility manifest types; do not hand-type DTOs. |
| `execute-plans/src/lib/bff-v1/agora/dashboard.ts` | Present for widget validation; uses direct fetch with window-origin base resolution. | Not sufficient for parent identity/servant acceptance. Do not copy its direct-fetch pattern blindly into the shell. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes. | Not sufficient for parent acceptance; Ask route usage must be gated behind servant/session readiness. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing Ask UI still present. | Parent shell must gate it while `AG-BE-ID-003` is blocked. |

Current source scan from this sidecar confirms:

```text
execute-plans/src/agora/pages/AskPersonas.tsx
execute-plans/src/agora/widgets/*
execute-plans/src/agora/dashboard/*
execute-plans/src/entries/agora-main.tsx
execute-plans/src/lib/bff-v1/agora/contract-snapshot.json
execute-plans/src/lib/bff-v1/agora/dashboard.ts
execute-plans/src/lib/bff-v1/agora/types.ts
execute-plans/src/lib/bff/agora.ts
```

`AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing.

## 7. Updated Minimal Status-Shell Contract

If parent `AG-FE-ID-001` proceeds before `AG-BE-ID-003` clears, the safe
frontend shape is no longer "servant backend not ready". It is now "servant
profile ready, servant sessions not ready":

```text
agora-main.tsx
  -> AgoraApp.tsx
     -> identity.getAgoraMe()
     -> identity.getAgoraCapabilities()
     -> servant.ensureAgoraServant({ idempotencyKey, requestId })
     -> current 200 maps to servant_profile_ready
     -> Ask/session/command surfaces remain disabled or read-only
        while AG-BE-ID-003 is blocked
```

Required shell states:

| State | Trigger | UI/runtime rule |
|---|---|---|
| Auth blocked | Missing auth or `401` from readiness/ensure call. | Render blocked auth state; no servant/session controls. |
| Scope/audience blocked | `403`, wrong tenant, wrong audience, or missing Agora capability. | Render blocked scope state; no seed/mock retry. |
| Identity ready | `/me` and `/capabilities` succeed. | Show tenant/user predicate, granted capabilities, and servant policy facts. |
| Servant profile ready | `/servant/ensure` returns 200 with `ServantProfile`. | Show servant persona/status/policy; no broker, capital, RuntimeBinding, or order authority. |
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`; current tests cover 422 for missing idempotency key. | Treat as client implementation defect; do not retry with mock data. |
| OpenClaw sync degraded | Runtime returns 503 dependency unavailable during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked or unmerged. | Keep Ask/session/command surfaces disabled or read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 8. Operator Journey

### Current honest journey

```text
Operator opens agora.html
  -> Agora bundle loads from the separate Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through a strict identity client
  -> BFF returns tenant_id, user_id, fail-closed read_predicate,
     frozen Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict identity client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure with Idempotency-Key
     and X-Request-Id; current runtime/tests do not require a request body
  -> BFF creates or reconciles one user-private agora_servant persona,
     syncs OpenClaw agent metadata, and returns a 200 ServantProfile envelope
  -> shell renders servant profile ready and no-authority policy facts
  -> Ask/session/command surfaces remain disabled or read-only because
     AG-BE-ID-003 is blocked
```

### Future servant session journey, still blocked

```text
AG-BE-ID-003 resolves the session_type contract disposition
  -> runtime implements POST /bff/agora/servant/sessions and companion routes
  -> frontend adds servant-session clients under src/lib/bff-v1/agora/*
  -> shell can open interactive/trainer/research_task sessions only through
     the approved BFF facade
  -> SSE stream and message posting use the audited session identity
  -> AskPersonas or replacement command UI is enabled only after session
     readiness is proven
```

## 9. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent dependency disposition | Parent either waits for `AG-BE-ID-003`, or explicitly narrows completion to an identity plus servant-profile status shell while leaving sessions disabled. |
| Identity route truth | Parent states `/me` and `/capabilities` are interim runtime routes, not generated OpenAPI operations. |
| Servant ensure truth | Parent updates stale 501 assumptions and proves `/servant/ensure` success, 422 header validation, and 503 dependency failure handling where applicable. |
| Header discipline | `servant.ts` supplies `Idempotency-Key` and `X-Request-Id`; tests cover missing-header behavior. |
| Ensure contract/runtime mismatch | Parent explicitly notes that current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI declares a required body and 201 new-create response. |
| Type mirror truth | Parent reuses generated v1.1 types from `types.ts`, while distinguishing generated operation inventory from runtime implementation completeness. |
| Runtime session gap | Parent does not call `/bff/agora/servant/sessions*` until `AG-BE-ID-003` lands. |
| Strict clients | `identity.ts` and `servant.ts` use strict live semantics, do not fall back to mock/seed data, and keep page components away from direct route fetches. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply servant-session readiness while `AG-BE-ID-003` is blocked. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import broad Management/capital/broker/RuntimeBinding path helpers. |
| Dashboard separation | Parent does not use completed dashboard route/widget work as proof of servant-session readiness. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant ensure success, servant header validation, OpenClaw degraded handling, no forbidden imports, and no forbidden bundle strings. |

## 10. Suggested Parent Verification

Backend current-state checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .
rg -n "/bff/agora/me|/bff/agora/capabilities" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/openapi/agora_v1.openapi.yaml execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/bff/agora/router.py
rg -n "ensureAgoraServant|createServantSession|ServantProfile|ServantSessionCreateRequest|session_type" execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/bff/agora/servant/router.py docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md
```

Frontend parent checks after implementation:

```bash
test -f execute-plans/src/agora/AgoraApp.tsx
test -f execute-plans/src/lib/bff-v1/agora/identity.ts
test -f execute-plans/src/lib/bff-v1/agora/servant.ts
rg -n "@/lib/bff/agora|fetch\\(" execute-plans/src/agora/AgoraApp.tsx execute-plans/src/lib/bff-v1/agora/identity.ts execute-plans/src/lib/bff-v1/agora/servant.ts
rg -n "management|RuntimeBinding|capital|broker|order" execute-plans/src/agora/AgoraApp.tsx execute-plans/src/lib/bff-v1/agora/identity.ts execute-plans/src/lib/bff-v1/agora/servant.ts
npm --prefix execute-plans test -- src/agora
npm --prefix execute-plans run build:agora
```

Expected current interpretation:

- `/bff/agora/me` and `/bff/agora/capabilities` are runtime-only readiness
  routes absent from generated operation inventory.
- `/bff/agora/servant/ensure` is implemented and tested as a successful
  create/reconcile path, with observed no-body 200 behavior that differs from
  the v1.1 OpenAPI body/status description.
- `getAgoraServant`, `reconcileAgoraServant`, and servant session operations are
  present in the v1.1 contract/type inventory but do not have current runtime
  handlers in the servant sub-router.
- `ServantSessionCreateRequest` still lacks `session_type`; `AG-BE-ID-003`
  remains blocked on that design decision.
- Parent frontend target files are still missing.

## 11. Sidecar Verification

Commands run from branch
`task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` after fast-forwarding to
`origin/dev` at `ac0d55c1bffdd5791c529cc915ca531e08c2c8d2`:

```bash
git status -sb
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DB-001
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_10.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md
```

Results:

- Branch confirmed correct and fast-forwarded to `origin/dev`.
- Task state confirmed: sidecar `in_progress`, parent `todo`, `AG-BE-ID-002`
  archived `done`, `AG-BE-ID-003` blocked, `AG-XR-003` blocked,
  `AG-FE-DB-001` archived `done`.
- `35 passed in 20.86s` for the focused BFF/identity/servant/OpenClaw adapter
  test set.
- Frozen v1 schema bundle verify passed for 15 files.
- v1.1 OpenAPI YAML parse passed.
- Agora generated types check passed: `17 schemas, 96 operations`.
- `git diff --check` passed for the task-owned files.

## 12. Reviewer Handoff

Reviewer `Claude` should review this packet as a support-only followup for
`AG-FE-ID-001`.

Approve this sidecar if:

1. the delta from FOLLOWUP-9 is accurate, especially the transition from
   501-only servant ensure to successful `/servant/ensure`;
2. the remaining `AG-BE-ID-003` session facade blocker is correctly described;
3. the parent handoff does not modify canonical truth or runtime implementation;
4. the parent absorption checklist is sufficient for `AG-FE-ID-001` owner
   implementation or blocker disposition.

Reviewer `Claude` approved this packet with no requested content changes in
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10-REVIEW.md`.

## 13. Owner Closeout Note

Owner `Codex` re-read the task brief, support packet, and reviewer approval
record during `owned_finalize_dispatch` closeout. The closeout change is limited
to task-scoped support metadata and the task brief review status. Canonical
truth, runtime implementation, OpenAPI, capability manifests, and frontend
source remain unchanged.

Closeout verification:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_10.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md
```

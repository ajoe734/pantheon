# AG-FE-ID-001 Followup-11 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex2` / `Claude` |
| Date | `2026-06-20` |
| Status | `ready for reviewer handoff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, registry code,
governance implementation, OpenClaw adapter code, or execute-plans source.

## 1. Purpose

This eleventh followup updates the `AG-FE-ID-001` BFF/frontend handoff after
`origin/dev` advanced to merge commit
`18f5bcebe06c0cd4ef0121a4b77de142b5909553` via PR #1874.

The important delta from FOLLOWUP-10 is not a new frontend implementation. It is
the newly merged support finding for `AG-BE-ID-003`:

1. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` is archived `done` and merged
   through PR #1874. It confirms all six original session-facade gaps remain
   open.
2. That packet adds three session-routing findings the parent frontend must
   absorb: the parallel `/bff/agora/ask/sessions` surface, the non-canonical
   `quick_ask` default, and stale ownership wording in `agora/identity/router.py`.
3. Parent `AG-FE-ID-001` is still `todo` and still depends on `AG-BE-ID-003`.
4. `AG-BE-ID-003` itself is still `blocked`, waiting for `Claude`, because the
   canonical v1.1 servant session create schema still lacks `session_type`.
5. The required parent frontend targets remain missing:
   `AgoraApp.tsx`, `identity.ts`, and `servant.ts`.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | `in_progress` | This packet is the intended deliverable. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | archived `done`; PR #1866 merged | Previous packet remains accurate except where this packet adds the PR #1874/session-surface delta. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-BE-ID-002` | archived `done`; merge target `dev` at `247211c2208d15bce628c017044a3bf2062603e6` | `/bff/agora/servant/ensure` is a real successful runtime path. |
| `AG-BE-ID-003` | `blocked`; waiting for `Claude` | Servant session facade remains unavailable pending `session_type` contract disposition. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | archived `done`; PR #1874 merged | Latest session-facade support finding is now on `dev` and should be treated as the current downstream FE gate reference. |
| `AG-XR-003` | `blocked`; waiting for `Claude2` | Compatibility manifest work is not a complete deployment/type-mirror proof while broader execute-plans gate remains blocked. |
| `AG-FE-DB-004` | archived `done` | Dashboard proposal/change-log work is complete, but remains separate from identity/servant/session shell readiness. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The servant profile can be ensured, but session creation,
message posting, terminate, and session-scoped SSE cannot be treated as ready.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_11.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Confirms owner, reviewer, artifact, and in-progress state. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Confirms previous packet archived `done` via PR #1866. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `blocked` on the missing `session_type` contract decision. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Confirms PR #1874 support packet is archived `done` and merged to `dev`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md` | Previous approved handoff baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10-REVIEW.md` | Previous review approval record. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Latest merged session-facade BFF/frontend support packet. |
| `services/control-plane/bff/agora/router.py` | Runtime identity/capability readiness routes. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant ensure implementation. |
| `services/control-plane/bff/main.py` | Existing partial Agora session and ask/session routes. |
| `services/control-plane/bff/agora/identity/router.py` | Migration placeholder that still lists session and ask/session routes. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 servant/session/workshop/dashboard route artifact. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Current generated Agora v1.1 snapshot in this checkout. |
| `execute-plans/src/entries/agora-main.tsx` | Current Agora entry still renders `AskPersonas` directly. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing Ask UI still imports the legacy `@/lib/bff/agora` helper and assistant catalog surfaces. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch Ask helper. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-10

| Change | What changed | Parent implication |
|---|---|---|
| Branch currentness | This worktree is current with `origin/dev` at merge commit `18f5bceb` from PR #1874. | Parent handoff now includes the latest merged `AG-BE-ID-003` followup findings. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` closed | The followup is archived `done`; closeout says PR #1874 merged and support-only boundary was confirmed. | Treat it as the current session-facade support reference for frontend gating. |
| Session gaps re-confirmed | Terminate route, session type validation/OpenClaw mapping, audit fields, degraded error, session-scoped SSE, and session module migration remain open. | Parent must keep Ask/session/command controls disabled or read-only. |
| New `ask/sessions` split | `/bff/agora/ask/sessions` is a separate quick-ask surface that filters `mode == "quick_ask"` and has its own close route. | Parent must not route `interactive`, `trainer`, or `research_task` creation through this surface without a backend disposition. |
| Non-canonical default | `POST /bff/agora/sessions` still defaults missing `mode`/`sessionType` to `"quick_ask"`, which is not one of the SD 5.3 types. | Parent session client must wait for required `session_type` validation or explicitly show session creation unavailable. |
| Stale migration scope | `agora/identity/router.py` still lists `ask/sessions` routes as migration-pending, but followup-2 scopes them to the ASK layer unless explicitly reassigned. | Parent should not ask frontend to consume an ownership-ambiguous session route. |
| Servant ensure implementation unchanged | Runtime still accepts no body, requires `Idempotency-Key` and `X-Request-Id`, and returns 200 for create and reconcile. | Parent `servant.ts` can target observed runtime behavior but must flag the OpenAPI body/status mismatch. |
| Test docstring stale | `test_agora_router.py` header still says `/servant/ensure` returns 501, while actual tests now assert 200. | Parent reviewers should trust executable assertions and runtime code, not the stale file header. |
| Frontend target files | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing. | Parent cannot close until these files or approved equivalents are implemented and tested. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy. | Still not listed as an operation in `agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, or generated `types.ts`. | Parent may use it as accepted interim runtime route truth for identity readiness. Keep the client narrow and document runtime-only operation status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Parent may use it for readiness/capability display. Do not claim generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; creates or reconciles one user-private servant persona and syncs OpenClaw. Current tests post no body and receive `200` for both create and reconcile. | Present in v1.1 OpenAPI and generated `types.ts` as `ensureAgoraServant`; OpenAPI declares required `ServantEnsureRequest` body and `201` for new provisioning. | `servant.ts` should post with `Idempotency-Key` and `X-Request-Id`, parse current 200 `ServantProfile`, and handle 401/403/422/503 explicitly. It should record the body/status mismatch instead of assuming generated semantics are exact. |
| `GET /bff/agora/servant` | No runtime handler found in the current servant sub-router. | Present in v1.1 OpenAPI and generated `types.ts` as `getAgoraServant`. | Do not build parent shell logic that depends on this read route until runtime implementation lands. Use `/ensure` for current servant status proof only if the parent accepts the side effect. |
| `POST /bff/agora/servant/reconcile` | No runtime handler found in the current servant sub-router. | Present in v1.1 OpenAPI and generated `types.ts`. | Keep out of parent UI until runtime support exists. |
| `POST /bff/agora/servant/sessions*` | Not implemented in the current servant sub-router. `AG-BE-ID-003` is blocked before implementation. | Present in v1.1 OpenAPI and generated `types.ts`, but `ServantSessionCreateRequest` lacks `session_type`. | Do not call these routes from the parent frontend until `AG-BE-ID-003` lands and the schema decision is resolved. |
| `GET/POST /bff/agora/sessions*` | Existing legacy routes live in `main.py`; create accepts `mode`/`sessionType`, performs no canonical enum enforcement, and defaults to `quick_ask`. | Not the v1.1 servant session facade. | May remain useful for legacy reads, but parent must not use it as proof of interactive/trainer/research_task readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; filters to `mode == "quick_ask"` and has a close route for that mode only. | Ownership is ASK/SEM unless explicitly reassigned; not a multi-type servant session facade. | Do not use for `interactive`, `trainer`, or `research_task` parent controls. |
| `GET /bff/sse/agora/sessions/{sessionId}` | Registered in `main.py`, but aliases to shared ask-channel stream and does not use `sessionId` for filtering. | Session-scoped SSE remains a gap. | Parent should surface `session_stream_unavailable` until a scoped stream lands. |
| Dashboard recipe/widget routes | Runtime/dashboard frontend work has advanced and `AG-FE-DB-004` is archived `done`. | Present in v1.1 OpenAPI and generated `types.ts`. | Dashboard readiness remains separate from identity/servant/session shell readiness. |

The safe BFF facts today are: user-private identity scope, filtered capability
readiness, successful servant ensure/provision/reconcile through `/ensure`, and
explicit absence of a validated servant session facade.

## 6. Session-Facade Blockers That Matter To The Frontend

`AG-BE-ID-003` is not just waiting on one missing field. The latest merged
support packet records a broader set of session blockers:

| Blocker | Frontend consequence |
|---|---|
| `ServantSessionCreateRequest` has `intent`, `strategy_ref`, and `metadata`, but no `session_type`; `additionalProperties: false`. | Parent cannot send the required `interactive`, `trainer`, or `research_task` type through the v1.1 servant session contract. |
| `POST /bff/agora/sessions` defaults to `quick_ask` when type is omitted. | A frontend that omits type will silently create a non-canonical session. |
| `/bff/agora/ask/sessions` is quick-ask-only and filters by `mode == "quick_ask"`. | It cannot satisfy the parent command/session journey for the three canonical session types. |
| `POST /bff/agora/sessions/{sessionId}/terminate` is absent. | Parent must not show a live terminate button. |
| Session writes do not carry all audit fields from the AG-BE-ID-003 acceptance criteria. | Parent must not claim session write audit readiness. |
| Session route errors do not expose `OPENCLAW_UPSTREAM_DEGRADED`. | Parent must not show a tested OpenClaw degradation state for sessions yet. |
| `GET /bff/sse/agora/sessions/{sessionId}` is a shared ask-channel alias. | Parent must not claim session-scoped live updates. |
| Session logic still lives in `main.py`; package module ownership remains unresolved. | Frontend should wait for a stable BFF facade boundary before adding strict session clients. |

Recommendation to parent: implement only the identity plus servant-profile
status shell until `AG-BE-ID-003` is merged and reviewer-approved. Keep
Ask/session/command-line UI disabled or read-only and explicitly label the
session facade unavailable.

## 7. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Exists and renders `AskPersonas` directly. | Parent should route through `AgoraApp.tsx` or an approved equivalent status shell before exposing ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | **MISSING**. | Parent must add the shell or block for missing design/spec authority. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | **MISSING**. | Parent should add narrow clients for `/me` and `/capabilities`; these are runtime routes, not generated operations. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | **MISSING**. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present; generated v1.1 snapshot with `contract_version: "1.1"`, `17` schemas, and `96` operations. | Reuse `ServantProfile` and operation inventory types; do not hand-type DTOs. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes and ask SSE. | Not sufficient for parent acceptance; Ask route usage must be gated behind servant/session readiness. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing Ask UI still imports `@/lib/bff/agora` plus assistant catalog surfaces. | Parent shell must gate it while `AG-BE-ID-003` is blocked; do not expose Management/kernel/provider controls as part of the Agora ID shell. |

Current source checks confirm `AgoraApp.tsx`, `identity.ts`, and `servant.ts`
are still missing in this checkout.

## 8. Updated Minimal Status-Shell Contract

If parent `AG-FE-ID-001` proceeds before `AG-BE-ID-003` clears, the safe
frontend shape remains:

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
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`; current tests cover 422 for missing idempotency key. | Treat as client implementation defect; no mock retry. |
| OpenClaw sync degraded | Runtime returns 503 dependency unavailable during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked, including `session_type`, `quick_ask`, terminate, audit, degradation, and SSE gaps. | Keep Ask/session/command surfaces disabled or read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 9. Operator Journey

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

### Future session journey, still blocked

```text
AG-BE-ID-003 resolves the BFF session contract
  -> no silent quick_ask default for canonical servant sessions
  -> parent owner declares sessions/ vs ask/sessions ownership
  -> runtime implements validated session_type creation for
     interactive, trainer, and research_task
  -> message writes carry the required audit fields
  -> terminate and session-scoped SSE are implemented
  -> OPENCLAW_UPSTREAM_DEGRADED is reachable and tested
  -> frontend adds strict session clients under src/lib/bff-v1/agora/*
  -> AskPersonas or replacement command UI is enabled only after readiness
     is proven
```

## 10. Parent Absorption Checklist

Claude should not absorb this sidecar into parent implementation unless the
parent evidence answers these checks:

| Check | Required evidence |
|---|---|
| Parent dependency disposition | Parent either waits for `AG-BE-ID-003`, or explicitly narrows completion to an identity plus servant-profile status shell while leaving sessions disabled. |
| Identity route truth | Parent states `/me` and `/capabilities` are interim runtime routes, not generated OpenAPI operations. |
| Servant ensure truth | Parent proves `/servant/ensure` success, 422 header validation, and 503 dependency failure handling where applicable. |
| Ensure contract/runtime mismatch | Parent explicitly notes current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI declares a required body and 201 new-create response. |
| Type mirror truth | Parent reuses generated v1.1 types from `types.ts`, while distinguishing generated operation inventory from runtime implementation completeness. |
| Runtime session gap | Parent does not call v1.1 `/bff/agora/servant/sessions*` until `AG-BE-ID-003` lands and `session_type` disposition is resolved. |
| Legacy session gap | Parent does not treat `main.py` `/bff/agora/sessions*` as canonical servant-session readiness while it defaults to `quick_ask` and lacks audit/degradation/terminate/SSE guarantees. |
| Ask session split | Parent does not use `/bff/agora/ask/sessions*` for `interactive`, `trainer`, or `research_task` controls without explicit backend ownership disposition. |
| Strict clients | `identity.ts` and `servant.ts` use strict live semantics, do not fall back to mock/seed data, and keep page components away from direct route fetches. |
| Ask/session gating | `AskPersonas` is gated behind shell status and cannot imply servant-session readiness while `AG-BE-ID-003` is blocked. |
| No broad path import | `AgoraApp.tsx`, `identity.ts`, and `servant.ts` do not import broad Management/capital/broker/RuntimeBinding path helpers. |
| Dashboard separation | Parent does not use completed dashboard route/widget work as proof of servant-session readiness. |
| Bundle isolation | `npm run build:agora` followed by forbidden-string scan has no Management/capital/broker/RuntimeBinding leakage, excluding explicitly reviewed inert schema text. |
| Tests | Frontend tests cover identity success, auth blocked, scope/audience blocked, strict BFF failure, servant ensure success, servant header validation, OpenClaw degraded handling, no forbidden imports, and no forbidden bundle strings. |

## 11. Suggested Parent Verification

Backend current-state checks:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .
rg -n "/bff/agora/me|/bff/agora/capabilities" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/openapi/agora_v1.openapi.yaml execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/bff/agora/router.py
rg -n "ensureAgoraServant|createServantSession|ServantProfile|ServantSessionCreateRequest|session_type|quick_ask|bff/agora/ask/sessions" execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/bff/agora/servant/router.py services/control-plane/bff/main.py services/control-plane/bff/agora/identity/router.py support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
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
- `getAgoraServant`, `reconcileAgoraServant`, and v1.1 servant session
  operations are present in the contract/type inventory but do not have current
  runtime handlers in the servant sub-router.
- Legacy `/bff/agora/sessions*` and `/bff/agora/ask/sessions*` routes exist in
  `main.py`, but do not satisfy the canonical servant-session parent gate.
- `ServantSessionCreateRequest` still lacks `session_type`; `AG-BE-ID-003`
  remains blocked on that design decision and on the additional session gaps.
- Parent frontend target files are still missing.

## 12. Sidecar Verification

Commands run from branch
`task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` after confirming
`origin/dev` at `18f5bcebe06c0cd4ef0121a4b77de142b5909553`:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin
git merge --ff-only origin/dev
AI_NAME=Codex2 ./scripts/ai-status.sh start AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11 "Started sidecar BFF/frontend handoff packet work; scope limited to support artifact."
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-DB-004
test -f execute-plans/src/agora/AgoraApp.tsx && echo AgoraApp_EXISTS || echo AgoraApp_MISSING
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && echo identity_EXISTS || echo identity_MISSING
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && echo servant_EXISTS || echo servant_MISSING
rg -n "bff/agora/sessions|bff/agora/ask/sessions|quick_ask|bff/sse/agora/sessions|terminate|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/bff/main.py services/control-plane/bff/agora/identity/router.py
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_11.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11.md
```

Results:

- Branch confirmed correct and already up to date with `origin/dev`.
- Task state confirmed: sidecar `in_progress`, parent `todo`, `AG-BE-ID-002`
  archived `done`, `AG-BE-ID-003` blocked, `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
  archived `done`, `AG-XR-003` blocked, `AG-FE-DB-004` archived `done`.
- `AgoraApp.tsx`: MISSING.
- `identity.ts`: MISSING.
- `servant.ts`: MISSING.
- Source grep confirms `quick_ask`, `ask/sessions`, and shared session SSE
  routes still exist in `main.py`; `OPENCLAW_UPSTREAM_DEGRADED` was not found
  in the checked session route surface.
- `35 passed in 19.49s` for the focused BFF/identity/servant/OpenClaw adapter
  test set.
- Frozen v1 schema bundle verify passed for 15 files.
- v1.1 OpenAPI YAML parse passed.
- Agora generated types check passed: `17 schemas, 96 operations`.
- `git diff --check` passed for the task-owned files.

## 13. Reviewer Handoff

Reviewer `Claude` should review this packet as a support-only followup for
`AG-FE-ID-001`.

Approve this sidecar if:

1. the delta from FOLLOWUP-10 is accurate, especially PR #1874 and the
   `AG-BE-ID-003` followup-2 findings;
2. the parent handoff correctly distinguishes implemented servant ensure from
   blocked session facade behavior;
3. the `quick_ask` and `ask/sessions` routing warnings are actionable for the
   parent frontend owner;
4. the packet does not modify canonical truth or runtime/frontend implementation.

Suggested approval command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh approve AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11 "Followup packet approved; support-only handoff accurately reflects PR #1874 session-surface findings, servant ensure readiness, AG-BE-ID-003 blocker state, and missing frontend targets."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11 "Describe the exact packet correction needed."
```

*Prepared by Codex2 for the `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` support slice.*

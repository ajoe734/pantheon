# AG-FE-ID-001 Followup-15 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude2` / `Claude` |
| Date | `2026-06-20` |
| Status | `done` |
| Current dev base | `a21c72c33befdc7761f8bec6afd8b1983fd1d587` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance implementation, OpenClaw adapter code, database
migrations, or execute-plans source.

## 1. Purpose

This fifteenth followup refreshes the `AG-FE-ID-001` BFF/frontend handoff.
FOLLOWUP-14 is archived `done` via PR #1907 at merge commit `4178f919`, with
owner closeout finalization rebased against `origin/dev`
`80f2832373aa390a952d61022b50933a473171ca`. Current `origin/dev` for this
packet is `a21c72c33befdc7761f8bec6afd8b1983fd1d587` after merging PR #1906
(AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8 at `f49e257c`), PR #1909
(AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 at `f7d2a456`), and PR #1910
(AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5 at `a21c72c3`, unrelated).

The important deltas from FOLLOWUP-14 are:

1. FOLLOWUP-14 is archived `done`; its packet and closeout are durable on
   `dev`.
2. `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8` is archived `done` via PR #1906
   at merge `f49e257c`. Parent `AG-XR-003` remains `blocked` waiting for
   `Claude2` disposition and execute-plans PR #63.
3. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` has landed on `dev` via
   PR #1909 at merge `f7d2a456`. It is currently in `review` status (Codex2
   reviewing). It refreshes the session-gate blocker with a new v1.1
   capability/compatibility nuance: `AG-XR-OPENAPI-001` is archived `done`
   and the v1.1 capability manifest now declares `agora.servant.v1` with
   prefix `/bff/agora/servant`; however the core `session_type` contract
   decision remains open and `AG-BE-ID-003` remains `blocked` waiting for
   `Claude`.
4. Parent `AG-FE-ID-001` remains `todo` and target files are still absent.
5. No BFF runtime, OpenAPI, capability manifest, or execute-plans source
   changed since FOLLOWUP-14's base.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Claude2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` | `in_progress` → ready for review | This packet. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | archived `done`; packet PR #1907 merged at `4178f919` | Previous approved packet is durable on `dev`. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` remains a successful create/reconcile runtime path. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant session facade remains unavailable pending `session_type` contract disposition. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | archived `done`; packet PR #1901 / merge `4a6a593d` and closeout PR #1904 / merge `80f28323` | Archived session-gate predecessor. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | `review`; packet landed via PR #1909 / merge `f7d2a456`; Codex2 is reviewer | Refreshed session-gate support packet; adds v1.1 capability/compatibility nuance; not yet archived `done`. Core blocker unchanged. |
| `AG-XR-003` | `blocked`; owner `Codex`, reviewer `Claude2`, waiting for `Claude2` | Compatibility manifest/deployment gate remains blocked on execute-plans PR #63 and release gate. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-7` | archived `done`; closeout PR #1902 / merge `81b17d67` | Predecessor acceptance support packet. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8` | archived `done`; PR #1906 / merge `f49e257c` | Latest acceptance support packet; parent `AG-XR-003` remains blocked on Claude2 / execute-plans PR #63 disposition. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and v1.1 capability manifest are on `dev`. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The frontend may rely on identity readiness and servant profile
ensure, but it must not claim interactive, trainer, or research session
readiness.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_15.md` | This sidecar's support-only assignment. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` | Confirms owner, reviewer, artifact, and `in_progress` state. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Confirms previous packet archived `done` via PR #1907. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and depends on `AG-BE-ID-003`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile remains archived `done`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `blocked` on the missing `session_type` contract decision. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Confirms followup-6 is in `review` after landing via PR #1909 / merge `f7d2a456`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked waiting for Claude2. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8` | Confirms followup-8 is archived `done` via PR #1906. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI and capability manifest work is archived `done`. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md` | Previous approved handoff baseline. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md` | Newest session-gate support packet with v1.1 capability/compatibility nuance. |
| `services/control-plane/bff/agora/router.py` | Runtime identity/capability readiness routes. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant ensure implementation; no servant session routes. |
| `services/control-plane/bff/main.py` | Existing legacy Agora session, ask/session, and SSE routes. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 servant/session/workshop/dashboard route artifact. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Current generated Agora v1.1 snapshot in this checkout. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-14

| Change | What changed | Parent implication |
|---|---|---|
| Branch currentness | This task branch is current with `origin/dev` at `a21c72c33befdc7761f8bec6afd8b1983fd1d587` after merging PR #1906/#1907/#1909/#1910. | FOLLOWUP-15 closeout baseline includes all merges since FOLLOWUP-14. |
| FOLLOWUP-14 closed | Archived `done`; delivery records packet and closeout via PR #1907 / merge `4178f919`. | Treat FOLLOWUP-14 as accepted support evidence. |
| AG-XR-003 followup-8 closed | Archived `done` via PR #1906 / merge `f49e257c570252964191212af8c2fe915e1e8535`. | Latest acceptance support packet for AG-XR-003; parent remains blocked. |
| AG-BE-ID-003 followup-6 landed | Packet on `dev` via PR #1909 / merge `f7d2a456ecba80a9cc46250f63b30f13341fa0b5`; task commit `b843a4b475975de380bbd2b876b762952a56a2a6`. Status is `review` (Codex2 reviewing). | New session-gate nuance: v1.1 capability manifest now declares `agora.servant.v1` with `/bff/agora/servant`; still not implementation readiness. Core `session_type` blocker unchanged. |
| AG-XR-OPENAPI-001 closed | Archived `done`; v1.1 OpenAPI and v1.1 capability manifest are on `dev`. | Discovery context improved; does not remove the session contract blocker. |
| Parent AG-FE-ID-001 unchanged | Parent remains `todo` and target files are still absent. | There is still no `AgoraApp.tsx`, `identity.ts`, or `servant.ts` implementation to review or absorb. |
| AG-BE-ID-003 unchanged | Still `blocked` waiting for `Claude`; canonical create request lacks `session_type`. | Parent frontend must keep create/message/terminate/stream controls unavailable. |
| Servant ensure unchanged | Runtime still has successful `/bff/agora/servant/ensure` with required `Idempotency-Key` and `X-Request-Id`. | Parent `servant.ts` should target observed 200 create/reconcile behavior and explicit 401/403/422/503 handling. |
| Compatibility gate unchanged | Parent `AG-XR-003` remains blocked on Claude2 disposition and execute-plans PR #63 even after followup-8 support closeout. | Do not use compatibility manifest sidecar closeout as deployment readiness proof for the parent ID shell. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover envelope, tenant/user predicate, capabilities, and servant policy. | Still absent from generated operation inventory. | Parent may use it as accepted interim runtime route truth for identity readiness. Keep the client narrow and document runtime-only status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Parent may use it for readiness/capability display. Do not claim generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented in `services/control-plane/bff/agora/servant/router.py`; creates or reconciles one user-private servant persona and syncs OpenClaw. Current tests post no body and receive `200` for create and reconcile. | Present in v1.1 OpenAPI and generated `types.ts` as `ensureAgoraServant`; OpenAPI declares required `ServantEnsureRequest` body and `201` for new provisioning. | `servant.ts` should post with `Idempotency-Key` and `X-Request-Id`, parse current 200 `ServantProfile`, and handle 401/403/422/503 explicitly. Record the body/status mismatch instead of assuming generated semantics are exact. |
| `GET /bff/agora/servant` | No runtime handler found in the current servant sub-router. | Present in v1.1 OpenAPI and generated `types.ts` as `getAgoraServant`. | Do not build parent shell logic that depends on this read route until runtime implementation lands. |
| `POST /bff/agora/servant/reconcile` | No runtime handler found in the current servant sub-router. | Present in v1.1 OpenAPI and generated `types.ts`. | Keep out of parent UI until runtime support exists. |
| `POST /bff/agora/servant/sessions*` | Not implemented in the current servant sub-router. `AG-BE-ID-003` is blocked before implementation. | Present in v1.1 OpenAPI and generated `types.ts`, but `ServantSessionCreateRequest` lacks `session_type` and rejects undeclared fields. | Do not call these routes from the parent frontend until the contract decision lands and BFF implementation is review-approved. |
| `GET/POST /bff/agora/sessions*` | Existing legacy routes live in `main.py`; create accepts `mode`/`sessionType`, performs no canonical enum enforcement, and defaults to `quick_ask`. | Not the v1.1 servant session facade. | May remain useful for legacy reads, but parent must not use it as proof of interactive/trainer/research_task readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; filters to `mode == "quick_ask"` and has a close route for that mode only. | Ownership is ASK/SEM unless explicitly reassigned; not a multi-type servant session facade. | Do not use for `interactive`, `trainer`, or `research_task` controls. |
| `GET /bff/sse/agora/sessions/{sessionId}` | Registered in `main.py`, but aliases to shared ask-channel stream and does not use `sessionId` for filtering. | Session-scoped SSE remains a gap. | Parent should surface `session_stream_unavailable` until a scoped servant-session stream lands. |
| Dashboard recipe/widget routes | Runtime/dashboard frontend work has advanced separately. | Present in v1.1 OpenAPI and generated `types.ts`. | Dashboard readiness remains separate from identity/servant/session shell readiness. |

The safe BFF facts today are unchanged for the parent shell: user-private
identity scope, filtered capability readiness, successful servant
ensure/provision/reconcile through `/ensure`, and explicit absence of a
validated servant session facade.

## 6. Session Gate Update From AG-BE-ID-003 Followup-6

The newest session support packet on `origin/dev` is
`AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`. It is in `review` status
(not yet archived `done`). Its packet landed via PR #1909 / merge
`f7d2a456ecba80a9cc46250f63b30f13341fa0b5` and task commit
`b843a4b475975de380bbd2b876b762952a56a2a6`.

Key nuance that followup-6 adds beyond followup-5:

1. `AG-XR-OPENAPI-001` is archived `done`. The v1.1 capability manifest now
   declares `agora.servant.v1` with prefix `/bff/agora/servant`, which is an
   improvement in discovery context over the frozen v1 manifest.
2. `docs/contracts/agora/dev-compatibility-manifest.json` is still `pending`;
   blockers cite frontend generated contract commit/runtime placeholders and
   frontend types not yet a deployment-ready Agora v1.1 proof.
3. The core `AG-BE-ID-003` contract blocker is unchanged: `ServantSessionCreateRequest`
   still has no public `session_type` field and `additionalProperties: false`.

Followup-6 restates the decision matrix the frontend must wait on:

| Gate | Current blocker | Frontend rule |
|---|---|---|
| Session type field | `ServantSessionCreateRequest` allows only `intent`, `strategy_ref`, and `metadata`; `session_type` and `sessionType` are undeclared. | Do not send undeclared fields from FE. Wait for approved schema or server-side derivation. |
| Route family | Parent must freeze `/bff/agora/servant/sessions` vs legacy `/bff/agora/sessions` vs aliases. | Do not wire live create/message/terminate/stream clients until the parent route family is review-approved. |
| Research task mapping | Strategy-dialogue allows `interactive` and `trainer`; `research_task` owner mapping is not frozen. | Keep research-task session UI disabled even if interactive/trainer later unblock first. |
| v1.1 capability manifest | `agora.servant.v1` now correctly declared with `/bff/agora/servant`; v1 manifest still shows legacy prefixes. | Use v1.1 manifest for discovery hints only; do not claim compatibility proof until `AG-XR-003` clears. |
| Degraded error | BFF enum uses `DEPENDENCY_UNAVAILABLE`; acceptance requires `OPENCLAW_UPSTREAM_DEGRADED`. | Do not show tested session-level OpenClaw degradation state yet. |

If the parent later confirms `/bff/agora/servant/sessions` as canonical, the
future FE client should wait for an approved public shape equivalent to:

```ts
type ServantSessionType = "interactive" | "trainer" | "research_task";

createServantSession(input: {
  sessionType: ServantSessionType;
  intent: string;
  strategyRef?: string;
  metadata?: Record<string, unknown>;
}): Promise<ServantSessionEnvelope>;
```

That shape is a handoff expectation only. It is not a claim that the current
schema is ready.

## 7. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Exists in the frontend repo history and still needs a shell gate before ask/session behavior is exposed. | Parent should route through `AgoraApp.tsx` or an approved equivalent status shell before exposing ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | **MISSING** in this checkout. | Parent must add the shell or block for missing design/spec authority. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | **MISSING** in this checkout. | Parent should add narrow clients for `/me` and `/capabilities`; these are runtime routes, not generated operations. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | **MISSING** in this checkout. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present; generated v1.1 snapshot includes servant/session operation inventory. | Reuse generated types where applicable; do not hand-type DTOs. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes and ask SSE. | Not sufficient for parent acceptance; Ask route usage must be gated behind servant/session readiness. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing Ask UI remains outside the strict ID/servant shell contract. | Parent shell must gate it while `AG-BE-ID-003` is blocked. Do not expose Management/kernel/provider controls as part of the Agora ID shell. |

Current source checks confirm `AgoraApp.tsx`, `identity.ts`, and `servant.ts`
are still missing in this checkout.

## 8. Minimal Status-Shell Contract

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
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`. | Treat as client implementation defect; no mock retry. |
| OpenClaw sync degraded | Runtime returns 503 dependency unavailable during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked. | Keep Ask/session/command surfaces disabled or read-only. |
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
  -> BFF returns filtered capability manifest and backend scope;
     v1.1 manifest declares agora.servant.v1 with /bff/agora/servant
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
AG-BE-ID-003 resolves the servant session contract
  -> route family is frozen, preferably /bff/agora/servant/sessions
  -> session_type or an approved server-side derivation is present
  -> research_task maps to an approved OpenClaw skill/session kind
  -> runtime implements create/message/terminate/session-scoped stream
  -> message writes carry required audit fields
  -> OPENCLAW_UPSTREAM_DEGRADED or approved equivalent is reachable and tested
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
| Servant session contract | Parent does not send undeclared `session_type` or `sessionType` to `ServantSessionCreateRequest`; it waits for an approved schema or server-side derivation. |
| Route family decision | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without an explicit backend disposition. |
| Research task mapping | Parent does not show or call research-task sessions until the OpenClaw skill/session mapping is frozen. |
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
rg -n "ServantSessionCreateRequest|servant/sessions|session_type|quick_ask|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1_1.openapi.yaml execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/bff/main.py services/control-plane/bff/agora/servant/router.py support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
rg -n "agora.servant.v1|agora.servant|bff_path_prefixes" services/control-plane/specs/agora/v2/capability_manifest_v1_1.json services/control-plane/specs/agora/capability_manifest.json
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
- `ServantSessionCreateRequest` still lacks `session_type` and rejects
  undeclared fields; `AG-BE-ID-003` remains blocked on that design decision.
- Legacy `/bff/agora/sessions*` and `/bff/agora/ask/sessions*` routes exist in
  `main.py`, but do not satisfy the canonical servant-session parent gate.
- `AG-XR-OPENAPI-001` is archived `done`; the v1.1 capability manifest
  correctly declares `agora.servant.v1` with `/bff/agora/servant`.
- `AG-XR-003` remains blocked; cross-repo compatibility manifest is `pending`.
- `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` is on `dev` in `review`;
  it adds v1.1 capability nuance but does not resolve the session contract.
- Parent frontend target files are still missing.

## 12. Sidecar Verification

Commands run for this packet:

```bash
git branch --show-current
git status --short
git fetch origin dev && git rev-parse origin/dev
git log --oneline --decorate -10 origin/dev
git merge --no-edit origin/dev
git diff --name-only 80f2832373aa390a952d61022b50933a473171ca..a21c72c33befdc7761f8bec6afd8b1983fd1d587 -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora execute-plans/src/agora
AI_NAME=Claude2 ./scripts/ai-status.sh start AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15 "Starting followup-15..."
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
test -f execute-plans/src/agora/AgoraApp.tsx || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/identity.ts || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/servant.ts || echo MISSING
```

Results:

- Branch was correct: `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15`.
- Working tree had only untracked task brief; no stale staged changes.
- `origin/dev` fetched at `a21c72c33befdc7761f8bec6afd8b1983fd1d587`.
- Merged origin/dev: fast-forward adding AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
  packet and task brief, plus AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5 brief update.
- Delta check: no BFF runtime, OpenAPI, capability manifest, or execute-plans
  source changed since FOLLOWUP-14 base in the relevant areas.
- Parent `AG-FE-ID-001` confirmed `todo`.
- `AG-BE-ID-003` confirmed `blocked` on the `session_type` contract decision.
- `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` confirmed in `review`.
- `AG-XR-003` confirmed `blocked` waiting for Claude2.
- `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8` confirmed archived `done` via PR #1906.
- `AG-XR-OPENAPI-001` confirmed archived `done`.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` confirmed still missing.

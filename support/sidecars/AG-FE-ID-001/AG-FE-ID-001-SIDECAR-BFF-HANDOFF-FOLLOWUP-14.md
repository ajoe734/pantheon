# AG-FE-ID-001 Followup-14 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex2` / `Codex` |
| Date | `2026-06-20` |
| Status | `ready for reviewer handoff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance implementation, OpenClaw adapter code, database
migrations, or execute-plans source.

## 1. Purpose

This fourteenth followup refreshes the `AG-FE-ID-001` BFF/frontend handoff
after this task branch merged current `origin/dev` at merge commit
`81b17d678b4c029522a32eb26d9eb218a2350279` (PR #1902).

The relevant session-gate freshness update is
`AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` closeout PR #1897 at merge
commit `e51bc8fdcdce119bd66596367c468364d18bf835`, plus the newer
`AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` packet merged through PR #1901
at `4a6a593d0edd33e6ac4d3b17e533ff047dd38530`. Followup-5 is currently
`review`, pending Codex2 review, so this packet treats it as current
review-pending session-gate evidence rather than approved closure. The earlier
FOLLOWUP-14 baselines at `c9f6c2e5c4d340d97d1cbcaeacf8f82545eaa7a5` and
`2eae7afbb9323063a9369ae31dfd3f90acd0eba4` are historical and are not the
current freshness baseline for this packet.

The important delta from FOLLOWUP-13 is a downstream session-gate freshness
update, not a new BFF or frontend implementation:

1. FOLLOWUP-13 is archived `done`; its packet and closeout are durable on
   `dev`.
2. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` is archived `done`; it first
   clarified that the parent BFF session decision is now the v1.1
   `/bff/agora/servant/sessions` contract, where
   `ServantSessionCreateRequest` lacks `session_type` and has
   `additionalProperties: false`.
3. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` is archived `done`; its
   packet landed through PR #1895 / packet commit `7de50e42`, then its
   approved owner closeout landed through PR #1897 / merge
   `e51bc8fdcdce119bd66596367c468364d18bf835`. It refines the same blocker
   into a decision-ready matrix covering the public type contract,
   `research_task` skill mapping, capability manifest alignment, legacy route
   substitution risk, and `OPENCLAW_UPSTREAM_DEGRADED`.
4. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` is now present on
   `origin/dev` through PR #1901 / merge `4a6a593d`; active task status is
   `review` pending Codex2 review. It restates the same contract decision
   request and marks followup-4 as archived `done`.
5. Parent `AG-FE-ID-001` remains `todo` and still depends on `AG-BE-ID-003`.
6. `AG-BE-ID-003` itself remains `blocked`, waiting for `Claude`, on the
   servant session type contract disposition.
7. The required parent frontend targets remain missing:
   `AgoraApp.tsx`, `identity.ts`, and `servant.ts`.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | `review` after owner handoff | This refreshed packet is the intended deliverable for Codex review. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` | archived `done`; packet PR #1884 and closeout PR #1885 merged | Previous approved packet is now durable on `dev`. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` remains a successful create/reconcile runtime path. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant session facade remains unavailable pending `session_type` contract disposition. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | archived `done`; packet PR #1886 and closeout PR #1890 merged | Predecessor support finding; no longer the latest session-gate packet. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | archived `done`; packet PR #1895 and closeout PR #1897 / merge `e51bc8fd` merged | Archived session-gate closeout baseline for frontend handoff purposes. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | `review`; packet PR #1901 / merge `4a6a593d` merged | Latest review-pending session-gate packet; do not treat it as approved closure until Codex2 review completes. |
| `AG-XR-003` | `blocked`; waiting for `Claude2` | Compatibility manifest/deployment gate is still not complete frontend deployment proof. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6` | archived `done`; PR #1889, clarification PR #1891, and closeout PR #1892 merged | Predecessor acceptance support packet; does not unblock parent `AG-XR-003` or `AG-FE-ID-001`. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-7` | archived `done`; closeout PR #1902 / merge `81b17d67` merged | Latest acceptance support packet; parent `AG-XR-003` remains blocked on Claude2 / execute-plans PR #63 disposition. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The frontend may rely on identity readiness and servant profile
ensure, but it must not claim interactive, trainer, or research session
readiness.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_14.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Confirms owner, reviewer, artifact, and handoff-ready task identity before re-handoff. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` | Confirms previous packet archived `done` through PR #1884 and PR #1885. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile remains archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `blocked` on the missing `session_type` contract decision. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Confirms predecessor session sidecar archived `done` after PR #1886 and #1890. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Confirms followup-4 is archived `done` after closeout PR #1897 / merge `e51bc8fd`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirms newest session sidecar is `review`, merged through PR #1901, and pending Codex2 review. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6` | Confirms related acceptance sidecar is archived `done` without unblocking parent `AG-XR-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-7` | Confirms latest acceptance sidecar is archived `done` while parent `AG-XR-003` remains blocked. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md` | Previous approved handoff baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-13-REVIEW.md` | Previous explicit Claude approval record. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Predecessor servant-session contract blocker and frontend gate reference. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` | Archived servant-session contract decision matrix and frontend gate baseline. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` | Latest review-pending session-gate packet and decision request. |
| `services/control-plane/bff/agora/router.py` | Runtime identity/capability readiness routes. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant ensure implementation; no servant session routes. |
| `services/control-plane/bff/main.py` | Existing legacy Agora session, ask/session, and SSE routes. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 servant/session/workshop/dashboard route artifact. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Current generated Agora v1.1 snapshot in this checkout. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-13

| Change | What changed | Parent implication |
|---|---|---|
| Branch currentness | This task branch merged current `origin/dev` at `81b17d678b4c029522a32eb26d9eb218a2350279`. | FOLLOWUP-14 now starts from the current dev tip and includes followup-4 closeout PR #1897, review-pending followup-5 PR #1901, AG-XR followup-7 closeout PR #1902, and earlier unrelated support merges PR #1899/#1900. |
| Older baseline correction | The previous branch-currentness merges at `c9f6c2e5c4d340d97d1cbcaeacf8f82545eaa7a5` and `2eae7afbb9323063a9369ae31dfd3f90acd0eba4` are historical. | Do not use the previously recorded invalid full SHA or treat `2eae7afb`/`c9f6c2e5` as the latest dev baseline. |
| FOLLOWUP-13 closed | Archived `done`; delivery records packet PR #1884 and closeout PR #1885. | Treat FOLLOWUP-13 as accepted support evidence unless superseded by this packet. |
| AG-BE-ID-003 followup-3 closed | Archived `done`; it records packet PR #1886 and closeout PR #1890. | Treat it as predecessor evidence for the `servant/sessions` blocker, not the latest frontend gate reference. |
| AG-BE-ID-003 followup-4 closed | Artifact merged through PR #1895 / packet commit `7de50e42`, then closeout merged through PR #1897 / `e51bc8fdcdce119bd66596367c468364d18bf835`. | Archived baseline for the explicit type-contract decision, research mapping, capability manifest, legacy substitution, and degradation-code questions. |
| AG-BE-ID-003 followup-5 in review | Artifact merged through PR #1901 / `4a6a593d0edd33e6ac4d3b17e533ff047dd38530`; active status is `review` pending Codex2 review. | Treat it as the newest session-gate packet, but not as approved closeout or implementation readiness. |
| Parent AG-FE-ID-001 unchanged | Parent remains `todo` and target files are still absent. | There is still no `AgoraApp.tsx`, `identity.ts`, or `servant.ts` implementation to review or absorb. |
| AG-BE-ID-003 unchanged | Still `blocked` waiting for `Claude`; canonical create request lacks `session_type`. | Parent frontend must keep create/message/terminate/stream controls unavailable. |
| Servant ensure unchanged | Runtime still has successful `/bff/agora/servant/ensure` with required `Idempotency-Key` and `X-Request-Id`. | Parent `servant.ts` should target observed 200 create/reconcile behavior and explicit 401/403/422/503 handling. |
| Compatibility gate unchanged | Parent `AG-XR-003` remains blocked even after followup-7 support closeout. | Do not use compatibility manifest sidecar closeout as deployment readiness proof for the parent ID shell. |

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

## 6. Frontend Surface To Hand Off

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

## 7. Minimal Status-Shell Contract

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
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`; current tests cover missing idempotency key. | Treat as client implementation defect; no mock retry. |
| OpenClaw sync degraded | Runtime returns 503 dependency unavailable during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
| Session facade unavailable | `AG-BE-ID-003` remains blocked, including `session_type`, route-family, research-task mapping, OpenClaw degradation, and SSE gaps. | Keep Ask/session/command surfaces disabled or read-only. |
| BFF unavailable in strict mode | Network error or 5xx while live strict is configured. | Render unavailable state; no silent mock fallback. |

`servant_policy.execution_authority = "none"` and
`prohibited_authority = ["runtime_binding", "broker_order", "capital_binding"]`
may be displayed as safety facts. They must not become operator controls.

## 8. Session Gate Update From AG-BE-ID-003 Followup-5

The latest session support packet on `origin/dev` is
`AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`. Its packet landed through
PR #1901 / merge `4a6a593d0edd33e6ac4d3b17e533ff047dd38530` and task commit
`5feac63882551a884f7505e831fd227b36fd2d13`. Its active task status is
`review`, pending Codex2 review. FOLLOWUP-14 can absorb it as current
review-pending support evidence without treating it as approved closure or
parent implementation readiness.

Followup-5 carries forward followup-4's archived `ServantSessionCreateRequest`
blocker and restates the decision matrix the frontend must wait on:

| Gate | Current blocker | Frontend rule |
|---|---|---|
| Route family | Parent must freeze `/bff/agora/servant/sessions` vs legacy `/bff/agora/sessions` vs aliases. | Do not wire live create/message/terminate/stream clients until the parent route family is review-approved. |
| Session type field | `ServantSessionCreateRequest` allows only `intent`, `strategy_ref`, and `metadata`; `session_type` and `sessionType` are undeclared. | Do not send undeclared fields from FE. Wait for approved schema or server-side derivation. |
| Research task mapping | Strategy-dialogue allows `interactive` and `trainer`; `research_task` owner mapping is not frozen. | Keep research-task session UI disabled even if interactive/trainer later unblock first. |
| Capability manifest alignment | Frozen manifest still lists legacy session prefixes, not `/bff/agora/servant/sessions`. | Do not treat generated type inventory alone as capability/deployment proof. |
| Degraded error | Acceptance requires `OPENCLAW_UPSTREAM_DEGRADED`; current servant ensure uses `DEPENDENCY_UNAVAILABLE`, and no servant session route exists. | Do not show tested session-level OpenClaw degradation state yet. |

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
rg -n "ServantSessionCreateRequest|servant/sessions|session_type|quick_ask|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1_1.openapi.yaml execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/bff/main.py services/control-plane/bff/agora/servant/router.py support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
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
- Parent frontend target files are still missing.

## 12. Sidecar Verification

Commands run from branch
`task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` after merging current
`origin/dev` at `81b17d678b4c029522a32eb26d9eb218a2350279`:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin dev
git rev-parse origin/dev
git log --oneline --decorate -8 origin/dev
git merge --no-edit origin/dev
git show --oneline --stat --decorate e51bc8fd
git show --oneline --stat --decorate 4a6a593d
git show --oneline --stat --decorate 81b17d67
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-13
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-6
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-7
rg -n "@router\\.(get|post)|/bff/agora/servant|ensure|Idempotency-Key|X-Request-Id|DEPENDENCY_UNAVAILABLE" services/control-plane/bff/agora/router.py services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py
rg -n "ServantSessionCreateRequest|servant/sessions|session_type|sessionType|quick_ask|bff/sse/agora/sessions|OPENCLAW_UPSTREAM_DEGRADED|createServantSession" services/control-plane/openapi/agora_v1_1.openapi.yaml execute-plans/src/lib/bff-v1/agora/types.ts services/control-plane/bff/main.py services/control-plane/bff/agora/servant/router.py services/control-plane/specs/agora/capability_manifest.json support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
test -f execute-plans/src/agora/AgoraApp.tsx
test -f execute-plans/src/lib/bff-v1/agora/identity.ts
test -f execute-plans/src/lib/bff-v1/agora/servant.ts
find execute-plans/src/agora execute-plans/src/lib/bff-v1/agora execute-plans/src/lib/bff -maxdepth 3 -type f | sort
```

Additional validation:

```bash
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py integrations/openclaw/test_persona_agent_sync.py -q
python3 scripts/agora_schema_bundle.py --verify
python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root .
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_14.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md
```

Results:

- Branch was correct and merged current `origin/dev` at
  `81b17d678b4c029522a32eb26d9eb218a2350279`.
- The older `c9f6c2e5` and `2eae7afb` baselines are historical; the invalid
  previous full SHA for `2eae7afb` is no longer used.
- Parent `AG-FE-ID-001` remains `todo`.
- `AG-BE-ID-003` remains blocked on the `session_type` contract decision.
- `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` is archived `done` and is now
  predecessor evidence.
- `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` is archived `done`; its
  packet artifact is on `origin/dev` through PR #1895 / packet commit
  `7de50e42`, its closeout is on PR #1897 / merge `e51bc8fd`, and it is the
  archived session-gate closeout baseline for this handoff.
- `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` is `review`; its packet is on
  `origin/dev` through PR #1901 / merge `4a6a593d`, and it is the latest
  review-pending session-gate reference for this handoff.
- `AG-XR-003` remains blocked; its followup-6 and followup-7 support packets
  are archived `done` but do not unblock parent FE readiness.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing.
- Focused BFF/OpenClaw pytest passed: `35 passed in 13.86s`.
- Agora schema bundle verify passed.
- v1.1 OpenAPI YAML parse passed.
- Generated Agora types are current: `17` schemas and `96` operations.
- Scoped tracked diff check passed.

## 13. Reviewer Handoff

Reviewer: `Codex`

Please review this support packet for:

1. support-only scope compliance
2. correct absorption of FOLLOWUP-13, AG-BE-ID-003 FOLLOWUP-3, archived
   FOLLOWUP-4, and review-pending FOLLOWUP-5
3. accurate statement that parent `AG-FE-ID-001` remains `todo`
4. accurate statement that `AG-BE-ID-003` remains blocked on the servant
   session type contract decision
5. actionable frontend gate guidance for identity plus servant-profile shell
   without exposing live session behavior

Suggested approval command:

```bash
AI_NAME=Codex ./scripts/ai-status.sh approve AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14 "Support packet approved; scope is support-only; FOLLOWUP-13 plus AG-BE-ID-003 followup-3/followup-4/followup-5 are correctly absorbed with followup-5 still review-pending; parent AG-FE-ID-001 remains todo; AG-BE-ID-003 session gate remains blocked; frontend targets remain missing."
```

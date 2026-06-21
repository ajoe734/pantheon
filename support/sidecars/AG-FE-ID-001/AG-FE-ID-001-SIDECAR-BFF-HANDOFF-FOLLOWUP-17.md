# AG-FE-ID-001 Followup-17 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17` |
| Helper parent | `AG-FE-ID-001` - Agora auth/session/servant status shell |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Date | `2026-06-21` |
| Status | `review approved; owner closeout prepared` |
| Packet observation base | `b85ca678fc91dc011b64ea80b47f87c9cf0fc623` |
| Owner closeout start base | `99ae910dfd254b49501c8c9c00f909744fd62fff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
execute-plans source files.

## 1. Purpose

This seventeenth followup refreshes the `AG-FE-ID-001` BFF/frontend handoff
after the previous AG-FE-ID-001 support packet merged and after two
AG-BE-ID-003 support packets closed.

At packet-preparation time, the material delta from FOLLOWUP-16 was narrow:

1. `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-16` is archived `done` via
   PR #1925 at merge `b85ca678`.
2. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` is archived `done` via
   PR #1924 at merge `d11a6fc9`.
3. `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` is archived `done` via
   PR #1926 at merge `ccff7df1`.
4. Both AG-BE-ID-003 followups confirm no servant-session implementation
   delta and preserve the same parent blocker: `ServantSessionCreateRequest`
   has no approved public session type field or derivation rule.
5. At packet-preparation time, `origin/dev` and this task branch both pointed
   at `b85ca678`.
   A focused diff from `b85ca678..HEAD` over the relevant BFF/OpenAPI/Agora
   and support paths is empty.
6. Parent `AG-FE-ID-001` remains `todo`; the three requested frontend target
   files remain missing in this checkout.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17` | `review_approved` -> owner closeout | This packet, with Claude review approval recorded in the review artifact. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-16` | archived `done`; PR #1925 / merge `b85ca678` | Previous approved AG-FE-ID-001 packet is durable on `dev`. |
| `AG-FE-ID-001` | `todo`; owner `Claude`, reviewer `Codex`; depends on `AG-FE-000`, `AG-BE-ID-003` | Parent implementation has not started in durable task state. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` remains the merged servant ensure/provision/reconcile surface. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Servant session facade remains unavailable pending the session type contract decision. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | archived `done`; PR #1924 / merge `d11a6fc9` | Confirms zero implementation delta after followup-6 and restates the same blocker. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | archived `done`; PR #1926 / merge `ccff7df1` | Reconfirms zero implementation delta after followup-7 and keeps FE/operator gates conservative. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and capability manifest remain present on `dev`. |
| `AG-XR-003` | `blocked`; owner `Codex`, reviewer `Claude2`, waiting for `Claude2` | Cross-repo compatibility status remains pending/blocked at the execute-plans release gate. |

Dependency honesty rule: parent `AG-FE-ID-001` still depends on
`AG-BE-ID-003`. The frontend may rely on identity readiness and servant profile
ensure, but it must not claim interactive, trainer, or research session
readiness.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_17.md` | This sidecar's support-only assignment. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17` | Confirms owner, reviewer, active state, artifact, and support-only acceptance. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-16` | Confirms predecessor archived `done` through PR #1925 / merge `b85ca678`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms parent remains `todo` and still depends on `AG-BE-ID-003`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms session facade remains `blocked` on the missing session type contract decision. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Confirms followup-7 archived `done` via PR #1924. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | Confirms followup-8 archived `done` via PR #1926. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI and capability manifest work is archived `done`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-16.md` | Previous AG-FE-ID-001 approved support baseline. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-16-REVIEW.md` | Claude's review record for followup-16. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md` | Latest session-gate packet after followup-6 closeout. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md` | Latest session-gate packet after followup-7 closeout. |
| `git diff --name-only c0af1ff8..HEAD -- ...` | Shows the relevant post-FOLLOWUP-16-observation path deltas are support artifacts only. |
| `git diff --name-only b85ca678..HEAD -- ...` | Confirms no relevant source/support delta after the followup-16 merge commit. |
| Target file probes under `execute-plans/src/agora` and `execute-plans/src/lib/bff-v1/agora` | Confirm `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup-16

| Change | What changed | Parent implication |
|---|---|---|
| FOLLOWUP-16 closed | Archived `done`; PR #1925 merged at `b85ca678`. | Treat FOLLOWUP-16 as accepted support evidence on `dev`. |
| AG-BE-ID-003 followup-7 closed | Archived `done`; PR #1924 merged at `d11a6fc9`. | Confirms zero BFF/OpenAPI/Agora implementation delta after followup-6; parent session blocker unchanged. |
| AG-BE-ID-003 followup-8 closed | Archived `done`; PR #1926 merged at `ccff7df1`. | Reconfirms no new servant-session implementation and keeps frontend/operator gates conservative. |
| Packet observation base | Packet-preparation `HEAD` equaled `origin/dev` at `b85ca678`. | This packet started from the latest merged AG-FE-ID-001 support baseline available at preparation time. |
| No post-b85 relevant delta at preparation | `git diff --name-only b85ca678..HEAD` over BFF/OpenAPI/spec/execute-plans Agora/support paths was empty at packet preparation time. | No runtime, contract, capability, or frontend source change superseded FOLLOWUP-16 before this packet was written. |
| Parent AG-FE-ID-001 unchanged | Parent remains `todo`; `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing. | There is still no parent frontend implementation to review or absorb. |
| Parent AG-BE-ID-003 unchanged | Still `blocked` waiting for `Claude` because no public session type contract or derivation rule is approved. | Parent FE must keep create/message/terminate/stream controls disabled. |
| AG-XR-003 unchanged | Still `blocked`; execute-plans integration/release gate remains unresolved. | Do not claim strict v1.1 cross-repo compatibility or deployment readiness from sidecar closeout alone. |

## 5. BFF Query Ledger For Parent

| Route or surface | Runtime BFF status | Contract/generated status | Frontend handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover identity envelope, tenant/user predicate, capabilities, and servant policy. | Still not generated as an OpenAPI v1.1 operation. | Parent may use it as accepted interim runtime route truth for identity readiness. Keep the client narrow and document runtime-only status. |
| `GET /bff/agora/capabilities` | Implemented in `services/control-plane/bff/agora/router.py`; focused tests cover filtered capability manifest and backend scope. | Same as `/me`: runtime route, not generated operation coverage. | Parent may use it for readiness/capability display. Do not claim generated operation support. |
| `POST /bff/agora/servant/ensure` | Implemented and archived through `AG-BE-ID-002`; creates or reconciles one user-private servant persona and syncs OpenClaw agent metadata. | Present in v1.1 OpenAPI and generated mirrors as `ensureAgoraServant`; OpenAPI declares a required body and `201` for new provisioning, while current runtime/tests observe no-body `200`. | `servant.ts` should send `Idempotency-Key` and `X-Request-Id`, parse current `200` `ServantProfile`, handle 401/403/422/503 explicitly, and record the body/status mismatch. |
| `GET /bff/agora/servant` | No current servant sub-router handler was identified in the previous packets. | Present in v1.1 OpenAPI and generated mirrors as `getAgoraServant`. | Do not make parent shell depend on this read route until runtime support lands. |
| `POST /bff/agora/servant/reconcile` | No current servant sub-router handler was identified in the previous packets. | Present in v1.1 OpenAPI and generated mirrors. | Keep out of the parent UI until runtime support exists or reviewer records a disposition. |
| `POST /bff/agora/servant/sessions*` | Still no accepted BFF runtime implementation; parent `AG-BE-ID-003` is blocked before coding. | Present in v1.1 OpenAPI, but `ServantSessionCreateRequest` still lacks `session_type` and rejects undeclared top-level fields. | Do not call these routes from the parent frontend until `AG-BE-ID-003` lands and the contract decision is approved. |
| `GET/POST /bff/agora/sessions*` | Legacy routes live in `main.py`; create accepts `mode`/`sessionType` and defaults to `quick_ask`. | Not the v1.1 servant-session facade. | Do not treat these routes as proof of `interactive`, `trainer`, or `research_task` readiness. |
| `GET/POST /bff/agora/ask/sessions*` | Existing quick-ask surface in `main.py`; close/stream semantics are ask-channel oriented. | Ownership remains separate from the v1.1 servant-session facade unless explicitly reassigned. | Do not use for parent `interactive`, `trainer`, or `research_task` controls. |
| `GET /bff/sse/agora/sessions/{sessionId}` | Previous packets noted it aliases to a shared ask-channel stream and does not prove session-scoped servant streaming. | Session-scoped SSE remains a gap. | Parent should surface `session_stream_unavailable` until a scoped servant-session stream lands. |
| Dashboard recipe/widget routes | Runtime/dashboard work has advanced separately. | Present in v1.1 OpenAPI and generated mirrors. | Dashboard readiness remains separate from identity/servant/session shell readiness. |

The safe BFF facts for the parent shell are unchanged: user-private identity
scope, filtered capability readiness, successful servant profile
ensure/reconcile through `/ensure`, and no validated servant-session facade.

## 6. Session Gate Status After AG-BE-ID-003 Followups 7 And 8

`AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` and followup-8 both close as
support-only packets. Their shared finding is that no implementation or
contract delta changed the parent blocker:

| Gate | Current blocker | Frontend rule |
|---|---|---|
| Session type field | `ServantSessionCreateRequest` allows only `intent`, `strategy_ref`, and `metadata`; no approved `session_type` or equivalent top-level field exists. | Strict FE clients must not send undeclared top-level fields. |
| Public derivation rule | No reviewer-approved rule says how BFF derives `interactive`, `trainer`, or `research_task` from route/context. | FE must wait for explicit schema or derivation authority. |
| Research task mapping | Checked evidence names `interactive` and `trainer`; `research_task` skill/session ownership remains unresolved. | Research-task controls stay disabled. |
| Runtime route family | v1.1 OpenAPI lists `/bff/agora/servant/sessions*`, but BFF runtime implementation is not accepted. | Do not wire live create/message/terminate/stream clients. |
| Degraded error | The accepted parent wording requires `OPENCLAW_UPSTREAM_DEGRADED`; current runtime evidence has not proven that session-level code. | Do not display a tested session degradation state yet. |
| Cross-repo compatibility | `AG-XR-003` remains blocked and compatibility status remains pending. | Strict v1.1 live release claims stay gated. |

If the parent later approves an explicit public type field, the expected FE
client shape remains:

```ts
type ServantSessionType = "interactive" | "trainer" | "research_task";

createServantSession(input: {
  sessionType: ServantSessionType;
  intent: string;
  strategyRef?: string;
  metadata?: Record<string, unknown>;
}): Promise<ServantSessionEnvelope>;
```

The actual wire field must match the reviewer-approved OpenAPI/schema field.
This is a handoff expectation, not a claim that the current schema is ready.

## 7. Frontend Surface To Hand Off

| Surface | Current state | Required parent decision |
|---|---|---|
| `execute-plans/src/entries/agora-main.tsx` | Existing entry history still needs a shell gate before ask/session behavior is exposed. | Parent should route through `AgoraApp.tsx` or an approved equivalent status shell before exposing ask/session behavior. |
| `execute-plans/src/agora/AgoraApp.tsx` | **MISSING** in this checkout. | Parent must add the shell or block for missing design/spec authority. |
| `execute-plans/src/lib/bff-v1/agora/identity.ts` | **MISSING** in this checkout. | Parent should add strict clients for `/me` and `/capabilities`; these are runtime routes, not generated OpenAPI operations. |
| `execute-plans/src/lib/bff-v1/agora/servant.ts` | **MISSING** in this checkout. | Parent should add a strict ensure client for `/servant/ensure`, including idempotency/request headers and typed failure mapping. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Present in this checkout as the generated Agora mirror; compatibility is still gated by `AG-XR-003`. | Reuse generated types where applicable and distinguish generated inventory from runtime completeness. |
| `execute-plans/src/lib/bff/agora.ts` | Legacy direct-fetch helper for Ask routes in prior evidence. | Not sufficient for parent acceptance; Ask route usage must be gated behind servant/session readiness. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` | Existing Ask UI remains outside the strict ID/servant shell contract. | Parent shell must gate it while `AG-BE-ID-003` is blocked. |

Parent shell and clients must not import or expose Management, capital pool,
broker order, live order, or RuntimeBinding controls.

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
| Servant profile ready | `/servant/ensure` returns current runtime `200` with `ServantProfile`. | Show servant persona/status/policy; no broker, capital, RuntimeBinding, or order authority. |
| Servant ensure validation failed | Missing `Idempotency-Key` or `X-Request-Id`. | Treat as a client implementation defect; no mock retry. |
| OpenClaw sync degraded | Runtime returns a dependency failure during servant agent sync. | Show provisioning/reconcile failed state with no session controls. |
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
     Agora capabilities, and servant_policy
  -> frontend calls GET /bff/agora/capabilities through a strict identity client
  -> BFF returns filtered capability manifest and backend scope
  -> frontend calls POST /bff/agora/servant/ensure with Idempotency-Key
     and X-Request-Id
  -> BFF creates or reconciles one user-private agora_servant persona,
     syncs OpenClaw agent metadata, and returns current runtime 200
     ServantProfile envelope
  -> shell renders servant profile ready and no-authority policy facts
  -> Ask/session/command surfaces remain disabled or read-only because
     AG-BE-ID-003 is blocked
```

### Future session journey, still blocked

```text
AG-BE-ID-003 resolves the BFF session contract
  -> route family is frozen, preferably /bff/agora/servant/sessions
  -> approved public session_type/session_kind or deterministic derivation exists
  -> research_task maps to an approved OpenClaw skill/session kind
  -> runtime implements create/message/terminate/session-scoped stream
  -> message writes carry required audit fields
  -> OPENCLAW_UPSTREAM_DEGRADED or an approved equivalent is reachable and tested
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
| Servant ensure truth | Parent proves `/servant/ensure` success and typed 401/403/422/503 failure handling where applicable. |
| Ensure contract/runtime mismatch | Parent explicitly notes current runtime accepts no body and returns 200 for create/reconcile, while OpenAPI declares a required body and 201 new-create response. |
| Type mirror truth | Parent reuses generated v1.1 types where applicable, while distinguishing generated operation inventory from runtime implementation completeness. |
| Servant session contract | Parent does not send undeclared `session_type` or `sessionType` to `ServantSessionCreateRequest`; it waits for approved schema or derivation. |
| Route family decision | Parent does not mix `/bff/agora/servant/sessions`, legacy `/bff/agora/sessions`, and `/bff/agora/ask/sessions` without explicit backend disposition. |
| Research task mapping | Parent does not show or call research-task sessions until the OpenClaw skill/session mapping is frozen. |
| Legacy session gap | Parent does not treat `main.py` `/bff/agora/sessions*` as canonical servant-session readiness while it defaults to `quick_ask`. |
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
rg -n "/bff/agora/me|/bff/agora/capabilities" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/bff/agora/router.py
rg -n "ServantSessionCreateRequest|servant/sessions|session_type|quick_ask|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/bff/main.py services/control-plane/bff/agora/servant/router.py support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md
rg -n "agora.servant.v1|agora.servant|bff_path_prefixes" services/control-plane/specs/agora/v2/capability_manifest_v1_1.json services/control-plane/specs/agora/capability_manifest.json
```

Frontend parent checks after implementation:

```bash
for f in execute-plans/src/agora/AgoraApp.tsx execute-plans/src/lib/bff-v1/agora/identity.ts execute-plans/src/lib/bff-v1/agora/servant.ts; do if test -f "$f"; then echo "PRESENT $f"; else echo "MISSING $f"; fi; done
test -f execute-plans/src/lib/bff-v1/agora/types.ts
rg -n "@/lib/bff/agora|fetch\\(" execute-plans/src/agora/AgoraApp.tsx execute-plans/src/lib/bff-v1/agora/identity.ts execute-plans/src/lib/bff-v1/agora/servant.ts
rg -n "management|RuntimeBinding|capital|broker|order" execute-plans/src/agora/AgoraApp.tsx execute-plans/src/lib/bff-v1/agora/identity.ts execute-plans/src/lib/bff-v1/agora/servant.ts
npm --prefix execute-plans test -- src/agora
npm --prefix execute-plans run build:agora
```

Expected current interpretation:

- `/bff/agora/me` and `/bff/agora/capabilities` are runtime-only readiness
  routes absent from generated OpenAPI operation inventory.
- `/bff/agora/servant/ensure` is implemented and tested as a successful
  create/reconcile path, with observed no-body 200 behavior that differs from
  the v1.1 OpenAPI body/status description.
- `getAgoraServant`, `reconcileAgoraServant`, and v1.1 servant session
  operations are present in the contract/type inventory but should not be
  treated as current runtime readiness.
- `ServantSessionCreateRequest` still lacks `session_type` and rejects
  undeclared top-level fields; `AG-BE-ID-003` remains blocked on that design
  decision.
- Legacy `/bff/agora/sessions*` and `/bff/agora/ask/sessions*` routes exist in
  `main.py`, but do not satisfy the canonical servant-session parent gate.
- `AG-XR-OPENAPI-001` is archived `done`; the v1.1 capability manifest
  correctly declares `agora.servant.v1` with `/bff/agora/servant`.
- `AG-XR-003` remains blocked; cross-repo compatibility status remains pending.
- Parent frontend target files are still missing.

## 12. Sidecar Verification

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,240p' .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_17.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,240p' ai-status.json
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-16
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-003
git fetch origin dev
git rev-parse HEAD origin/dev
git log --oneline --decorate --max-count=20 c0af1ff82dbaf0c1e039fff2ced33304f06cc225..HEAD
git diff --name-only c0af1ff82dbaf0c1e039fff2ced33304f06cc225..HEAD -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora execute-plans/src/lib/bff-v1/agora execute-plans/src/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003 support/sidecars/AG-XR-003
git diff --name-only b85ca678fc91dc011b64ea80b47f87c9cf0fc623..HEAD -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora execute-plans/src/lib/bff-v1/agora execute-plans/src/agora support/sidecars/AG-FE-ID-001 support/sidecars/AG-BE-ID-003
test -f execute-plans/src/agora/AgoraApp.tsx
test -f execute-plans/src/lib/bff-v1/agora/identity.ts
test -f execute-plans/src/lib/bff-v1/agora/servant.ts
rg -n "ServantSessionCreateRequest|session_type|sessionType|OPENCLAW_UPSTREAM_DEGRADED|/bff/agora/servant/sessions|/bff/agora/me|/bff/agora/capabilities" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/bff services/control-plane/specs/agora/v2/capability_manifest_v1_1.json
```

Results:

- Branch was correct: `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17`.
- At packet-preparation time, `HEAD` and `origin/dev` both resolved to
  `b85ca678fc91dc011b64ea80b47f87c9cf0fc623`.
- Initial working tree had only this task's untracked generated task brief.
- Delta from FOLLOWUP-16 observation base `c0af1ff8` to packet-preparation
  `HEAD` was limited to support artifacts for AG-BE-ID-003 followups 7/8 and
  AG-FE-ID-001 followup-16; no BFF/OpenAPI/spec/execute-plans Agora source
  changed in the checked pathset.
- Delta from followup-16 merge `b85ca678` to packet-preparation `HEAD` was
  empty in the checked pathset.
- Parent `AG-FE-ID-001` confirmed `todo`.
- `AG-BE-ID-002` and `AG-XR-OPENAPI-001` confirmed archived `done`.
- `AG-BE-ID-003` confirmed `blocked` waiting for `Claude` on the session type
  contract decision.
- `AG-BE-ID-003` followups 7 and 8 confirmed archived `done`.
- `AG-XR-003` confirmed still `blocked`.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` confirmed missing.
- `execute-plans/src/lib/bff-v1/agora/types.ts` confirmed present.

## 13. Owner Closeout Addendum

Claude approved this packet in
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17-REVIEW.md`.
The owner closeout pass rechecked the active task state with
`AI_NAME=Codex2`, confirmed parent `AG-FE-ID-001` remains `todo`, and confirmed
`AG-BE-ID-003` remains `blocked` waiting for Claude on the servant-session type
contract decision.

Closeout start base:

- Before this closeout commit, local `HEAD` and `origin/dev` both resolved to
  `99ae910dfd254b49501c8c9c00f909744fd62fff`, the merge commit for PR #1927.
- Local task branch is ahead of `origin/task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17`
  by the merged dev commit only before this closeout commit.
- Dirty files before closeout were task-scoped: this packet, the generated task
  brief, and the Claude review artifact.
- No canonical truth, runtime code, OpenAPI, capability manifest, registry,
  governance, or execute-plans source file is changed by this closeout.

Closeout commands:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-003
git rev-parse HEAD origin/dev
git diff --check -- .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_17.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17.md support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17-REVIEW.md
```

## 14. Handoff

This packet is approved for support-only closeout. The intended parent use is
to absorb the current support-only BFF query ledger, disabled-session boundary,
and frontend shell checklist before any execute-plans code is written for
`AG-FE-ID-001`.

The key conclusion is unchanged but fresher: the parent can prepare a strict
identity plus servant-profile status shell, but it must not expose servant
session create/message/terminate/stream behavior until `AG-BE-ID-003` records
the session type contract decision and lands reviewed runtime support.

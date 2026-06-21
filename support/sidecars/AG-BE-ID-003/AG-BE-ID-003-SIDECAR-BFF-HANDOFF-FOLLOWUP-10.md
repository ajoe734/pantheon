# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 10

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-21` |
| Status | `review_ready` |
| Current dev base | `519aa95478c74f69813e76ff38d8f0ccc0dc4bba` |
| Previous sidecar closeout merge | `6a7b391f7fcea6273c8536f357b3b3d563dc86ed` |
| Previous packet merge | `7169f6b1eafb52474188ae69a4fee8681b2fc6a3` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
execute-plans source files.

## 1. Purpose

Followup 9 is archived `done`. Its support packet PR #1932 merged at
`7169f6b1eafb52474188ae69a4fee8681b2fc6a3`, and its owner closeout PR #1935
merged at `6a7b391f7fcea6273c8536f357b3b3d563dc86ed`.

Current `origin/dev` for this followup is
`519aa95478c74f69813e76ff38d8f0ccc0dc4bba`. Since the followup-9 closeout
merge, `dev` advanced through AG-FE-DB-002 support closeout,
AG-FE-ID-001 followup-19 review/closeout support commits, and AG-XR-003
acceptance followup-12 support closeout. A focused path check from
`6a7b391f..origin/dev` shows no changes in the BFF, OpenAPI, Agora spec,
compatibility manifest, AG-BE-ID-003 support path, or execute-plans Agora
mirror paths checked for this handoff.

This packet therefore carries no implementation delta. It refreshes the parent
and frontend handoff with the latest task state, keeps the parent blocked on
the servant session type-contract decision, and records the additional frontend
support context from AG-FE-ID-001 followup-19.

This packet does not approve, reopen, or implement parent `AG-BE-ID-003`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | active `in_progress`; owner `Codex`, reviewer `Codex2` | This packet is the support-only artifact for review. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | archived `done`; packet PR #1932 and closeout PR #1935 merged | Previous AG-BE-ID-003 handoff is durable and still says the parent is blocked. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Parent implementation must not proceed until the servant session type contract is decided. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` is the accepted upstream servant ensure/provision/reconcile surface. |
| `AG-XR-003` | `blocked`; owner `Codex`, reviewer `Claude2`, waiting for `Claude2` | Cross-repo compatibility/deployment gate remains unresolved. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` | archived `done`; closeout PR #1938 merged | Latest frontend support packet confirms frontend parent remains gated by AG-BE-ID-003. |
| `AG-FE-ID-001` | `todo`; depends on `AG-FE-000` and `AG-BE-ID-003` | Frontend parent implementation has not started in durable task state. |

Dependency honesty rule: the frontend may continue to use identity and servant
profile readiness as support context, but it must not claim interactive,
trainer, or research-task session readiness while `AG-BE-ID-003` is blocked.

## 3. Sources Checked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_10.md` | This task-scoped assignment and support-only boundary. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Confirms active task state, owner, reviewer, artifact, and support-only acceptance. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent remains blocked on the servant session type-contract decision. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | Confirms predecessor archived `done`, with PR #1932 and closeout PR #1935 merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` | Confirms latest frontend support packet is archived `done` through closeout PR #1938. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms frontend parent remains `todo` and still depends on `AG-BE-ID-003`. |
| `git rev-parse origin/dev` and `git rev-parse HEAD` | Confirms this task branch is at current `origin/dev`, `519aa954`. |
| `git log --oneline 6a7b391f..origin/dev` | Shows AG-FE-DB-002, AG-FE-ID-001, and AG-XR-003 support/acceptance closeouts after followup 9. |
| `git log --oneline 6a7b391f..origin/dev -- ...` | No post-followup-9 commits touched the checked BFF/OpenAPI/Agora/spec/compatibility/AG-BE-ID-003/execute-plans Agora pathset. |
| `git diff --name-only 6a7b391f..origin/dev -- ...` | Empty for the checked parent handoff pathset. |
| `git log --oneline 6a7b391f..origin/dev -- support/sidecars/AG-FE-ID-001 ...` | Shows only AG-FE-ID-001 followup-19 review and closeout support commits after followup 9. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | `ServantSessionCreateRequest` still lacks a public session type field while route family exists on paper. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant router still owns `/servant/ensure`; no servant-session runtime routes are implemented there. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 manifest still declares `agora.servant.v1` under `/bff/agora/servant`. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Compatibility status remains `pending` with frontend placeholder/type blocking reasons. |
| `services/control-plane/bff/main.py` | Legacy `/bff/agora/sessions*` routes and SSE alias remain in `main.py`; stream alias still ignores `sessionId`. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19.md` | Latest frontend handoff context; confirms target frontend files remain absent from checked execute-plans remotes and AG-BE-ID-003 stays blocking. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup 9

| Change | What changed | Parent implication |
|---|---|---|
| Followup 9 closed | Archived `done`; support PR #1932 and closeout PR #1935 are merged. | Treat followup 9 as accepted support evidence. |
| Dev advanced to `519aa954` | AG-FE-DB-002 support closeout, AG-FE-ID-001 followup-19 review/closeout, and AG-XR-003 acceptance followup-12 support closeout landed. | Additional support context landed, but no BFF/runtime/contract/session implementation changed in the checked parent pathset. |
| Checked parent pathset | No diff from `6a7b391f..origin/dev` over BFF, OpenAPI, Agora specs, compatibility manifest, AG-BE-ID-003 support, or execute-plans Agora mirror. | No new evidence unblocks AG-BE-ID-003. |
| Frontend support state | AG-FE-ID-001 followup-19 says the parent remains `todo`; checked execute-plans target files remain absent from `origin/main` and `origin/dev`. | Frontend remains downstream and must not enable servant-session UI before AG-BE-ID-003 lands. |
| Cross-repo compatibility | AG-XR-003 remains blocked even after its followup-12 support closeout; dev compatibility manifest remains `pending`. | Strict v1.1 release/readiness claims stay gated. |

## 5. Contract Decision Request

### D1 - Public create schema still has no type field

`ServantSessionCreateRequest` still allows only:

```yaml
intent:
  type: string
strategy_ref:
  type: string
metadata:
  type: object
  additionalProperties: true
additionalProperties: false
```

No `session_type`, `sessionType`, `session_kind`, or equivalent top-level field
is present. Because `additionalProperties` is `false`, strict clients cannot
send an undeclared top-level type field.

### D2 - OpenClaw session invocation still needs a type

The BFF-to-OpenClaw session creation path needs a deterministic session type
for `interactive`, `trainer`, and `research_task`. AG-BE-ID-003 cannot safely
invent that mapping during implementation.

### D3 - Research-task mapping remains unresolved

Checked evidence still names interactive/trainer-like surfaces, but no
reviewer-approved OpenClaw skill/session kind for `research_task` has been
recorded in the parent task state.

### D4 - Discovery and compatibility remain split

The v1.1 capability manifest advertises `/bff/agora/servant`, but
`docs/contracts/agora/dev-compatibility-manifest.json` remains `pending`.
Strict live frontend behavior should stay gated until compatibility resolves or
reviewer/ops records an explicit disposition.

### D5 - Frontend followup closeout is not backend readiness

AG-FE-ID-001 followup-19 is useful support evidence, but it did not add
servant-session BFF runtime behavior, OpenAPI changes, or executable frontend
session clients. It reinforces the downstream gate rather than unblocking it.

## 6. Decision Options For Parent Reviewer

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred because it is least ambiguous. |
| Add an explicit equivalent such as `session_kind` | Same result if the field is documented and mapped to OpenClaw. | Acceptable if reviewer names the field. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Acceptable only with a deterministic, documented derivation rule. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract unless explicitly promoted by reviewer. |
| Default all creates to one type | Quick to code. | Should stay blocked; it fails parent acceptance for three visible types. |

## 7. Current Route Evidence

| Surface | Current observation at dev `519aa954` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, get, messages, terminate, and stream. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks a session type field. | Blocks strict create UI and parent implementation review. |
| v1.1 capability manifest | Declares `agora.servant.v1` with `/bff/agora/servant`. | Correct discovery layer, not runtime proof. |
| Dev compatibility manifest | `compatibility_status` remains `pending`; frontend commit/type placeholders remain in blocking reasons. | Strict live frontend remains gated. |
| Servant router | Implements servant ensure/provision/reconcile behavior only. | AG-BE-ID-003 must compose with this route and not overwrite AG-BE-ID-002 behavior. |
| BFF runtime | No BFF implementation for `/bff/agora/servant/sessions` was found in checked runtime paths. | Parent still needs implementation after the type decision. |
| Legacy `/bff/agora/sessions` | Existing `main.py` route creates legacy ask/session records, accepting `mode` or `sessionType` and defaulting to `quick_ask`. | Not a safe substitute for servant sessions. |
| Legacy SSE alias | `GET /bff/sse/agora/sessions/{sessionId}` delegates to `stream_ask_events()` and does not use `sessionId`. | Not proof of servant session-scoped SSE. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` was not found in checked BFF runtime paths. | Parent must preserve accepted degradation semantics or get reviewer approval for an explicit mapping. |
| Frontend sidecar state | AG-FE-ID-001 followup-19 is done, but parent `AG-FE-ID-001` remains `todo` and depends on AG-BE-ID-003. | No frontend strict-live enablement yet. |

## 8. Frontend Handoff

Until the parent records the type-contract decision and lands the runtime route
family, execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Display capability readiness | `GET /bff/agora/capabilities` or capability manifest context | Discovery/readiness only; not session runtime proof. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged. |
| Use AG-FE-ID-001 support packets as planning inputs | AG-FE-ID-001 support artifacts through followup-19 | Handoff context only; not executable frontend proof. |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive servant session | No public create contract field or derivation rule for `interactive`. |
| Create trainer servant session | Same type blocker, though trainer appears in existing strategy/training surfaces. |
| Create research-task servant session | No named `research_task` skill/session mapping in checked evidence. |
| Send servant session message | OpenAPI path exists, but BFF implementation is absent. |
| Terminate servant session | OpenAPI path exists, but BFF implementation is absent. |
| Stream servant session events | OpenAPI path exists, but BFF implementation is absent and legacy SSE is not session-scoped. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` was not found in checked BFF runtime paths. |
| Claim strict v1.1 cross-repo compatibility | `AG-XR-003` and the dev compatibility manifest remain blocked/pending. |
| Start AG-FE-ID-001 session UI as though backend is ready | Frontend parent still depends on blocked AG-BE-ID-003. |

### Recommended client shape after parent decision

If the parent approves an explicit public type field, execute-plans can expose
an ergonomic client similar to:

```ts
type ServantSessionType = "interactive" | "trainer" | "research_task";

createServantSession(input: {
  sessionType: ServantSessionType;
  intent: string;
  strategyRef?: string;
  metadata?: Record<string, unknown>;
}): Promise<ServantSessionEnvelope>;

sendServantSessionMessage(
  sessionId: string,
  content: string,
  attachmentRefs?: string[],
): Promise<ServantMessageEnvelope>;

terminateServantSession(sessionId: string): Promise<ServantSessionEnvelope>;

streamServantSessionEvents(sessionId: string): EventSource;
```

The wire field must match the parent-approved OpenAPI/schema field exactly.

## 9. Operator Journey

### Before parent decision

1. Operator resolves Agora identity through `GET /bff/agora/me`.
2. Operator checks Agora capabilities through the approved runtime/discovery surface.
3. Operator ensures the private servant through `POST /bff/agora/servant/ensure`.
4. UI may show servant readiness and no-authority policy facts.
5. Session create/message/stream/terminate controls stay disabled with a
   backend-contract-unavailable state.

### After parent implementation

1. Operator resolves identity and ensures servant readiness.
2. Operator creates a servant session with the parent-approved representation
   of `interactive`, `trainer`, or `research_task`.
3. BFF records `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`,
   and `session_id` in response meta and audit trail.
4. BFF maps the session to the approved OpenClaw skill/session kind.
5. Operator sends messages; BFF records idempotency and dispatches work to the
   OpenClaw-backed session.
6. Frontend receives only events for that servant session through a
   session-scoped SSE route.
7. OpenClaw degradation returns a 503 preserving `OPENCLAW_UPSTREAM_DEGRADED`
   or an explicitly approved equivalent envelope.
8. Operator terminates the session; BFF persists terminal status and emits a
   terminal event.

## 10. Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| P0 upstream servant | Compose with merged `POST /bff/agora/servant/ensure` from AG-BE-ID-002. |
| P1 type contract | Record how the create request carries or derives `interactive`, `trainer`, and `research_task`. |
| P2 OpenAPI/schema alignment | Update or explicitly approve the public create contract; do not accept undeclared top-level fields. |
| P3 research mapping | Name the OpenClaw skill/session kind that owns `research_task`. |
| P4 package placement | Add servant-session logic without overwriting AG-BE-ID-002 ensure behavior. |
| P5 discovery source | Use v1.1 capability manifest or an explicit compatibility rule for servant sessions; do not rely on frozen v1 prefixes alone. |
| P6 cross-repo compatibility | Keep strict live FE gated until `AG-XR-003` compatibility status is compatible or reviewer/ops records an explicit disposition. |
| P7 audit fields | Include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` for reads/writes and response meta. |
| P8 degradation code | Preserve `OPENCLAW_UPSTREAM_DEGRADED` as the accepted session degradation code or get reviewer approval for a precise mapping. |
| P9 SSE scope | Implement servant session stream scoped by `session_id`; do not reuse the shared ask-channel stream as proof. |
| P10 legacy route policy | State whether legacy `/bff/agora/sessions` remains compatibility-only, becomes an alias, or is out of scope. |
| P11 frontend dependency | Do not unblock AG-FE-ID-001 session controls until AG-BE-ID-003 lands runtime/session contract and compatibility disposition. |
| P12 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, capability discovery, compatibility gating, and degradation. |

## 11. Review Ask

Codex2 should review only the sidecar packet boundary and factual handoff:

1. support-only scope is preserved
2. parent blocker restatement matches current task state
3. delta assessment after followup 9 is accurate
4. AG-FE-ID-001 followup-19 is treated as downstream support context, not runtime readiness
5. frontend/operator gates remain conservative
6. parent absorption gates are actionable and do not implement canonical truth

## 12. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
./scripts/git/task_start.sh "AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10"
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-9
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
git rev-parse origin/dev
git rev-parse HEAD
git log --oneline 6a7b391f7fcea6273c8536f357b3b3d563dc86ed..origin/dev
git log --oneline 6a7b391f7fcea6273c8536f357b3b3d563dc86ed..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora
git diff --name-only 6a7b391f7fcea6273c8536f357b3b3d563dc86ed..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora
git log --oneline 6a7b391f7fcea6273c8536f357b3b3d563dc86ed..origin/dev -- support/sidecars/AG-FE-ID-001 .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_19.md
git diff --name-only 6a7b391f7fcea6273c8536f357b3b3d563dc86ed..origin/dev -- support/sidecars/AG-FE-ID-001 .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_19.md
rg --files services/control-plane/bff/agora
rg -n "OPENCLAW_UPSTREAM_DEGRADED|servant/sessions|session_type|sessionType|ServantSessionCreateRequest" services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora
rg -n "stream_ask_events|/bff/sse/agora/sessions|/bff/agora/sessions|terminate" services/control-plane/bff/main.py
sed -n '188,224p' services/control-plane/openapi/agora_v1_1.openapi.yaml
sed -n '624,726p' services/control-plane/openapi/agora_v1_1.openapi.yaml
sed -n '1,260p' services/control-plane/bff/agora/servant/router.py
sed -n '1,220p' services/control-plane/specs/agora/v2/capability_manifest_v1_1.json
sed -n '1,200p' docs/contracts/agora/dev-compatibility-manifest.json
sed -n '20690,20860p' services/control-plane/bff/main.py
sed -n '42684,42708p' services/control-plane/bff/main.py
sed -n '1,260p' support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-19.md
```

No runtime tests were run because this sidecar changes only support artifacts.

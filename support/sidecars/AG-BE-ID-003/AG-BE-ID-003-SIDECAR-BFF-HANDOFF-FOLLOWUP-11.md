# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 11

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-21` |
| Status | `in_progress; ready for sidecar review after packet PR merge` |
| Current dev base | `97cfbdd5037a2cbe20143f24c2775954824e275a` |
| Previous sidecar closeout merge | `c009f0a5774a81af0686b3a6e4eda21881918e0e` |
| Previous packet merge | `997644ad1186ee9bbe3913f3e8ea447239a04cf0` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code, or
execute-plans source files.

## 1. Purpose

Followup 10 is archived `done`. Its packet PR #1940 merged at
`997644ad1186ee9bbe3913f3e8ea447239a04cf0`, and its owner closeout PR #1943
merged at `c009f0a5774a81af0686b3a6e4eda21881918e0e`.

Current `origin/dev` for this followup is
`97cfbdd5037a2cbe20143f24c2775954824e275a`. Since the followup-10 closeout,
`dev` advanced only through `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-20`
review/closeout support material and `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21`
packet support material. A focused path check from
`c009f0a5..origin/dev` shows no changes in the BFF, OpenAPI, Agora specs,
compatibility manifest, AG-BE-ID-003 support path, or execute-plans Agora
mirror paths checked for this handoff.

This packet therefore carries no implementation delta. It refreshes the parent
and frontend handoff with the latest task state, records that the downstream
frontend followup-20 packet is archived `done`, records that followup-21 is a
support-only frontend review packet, and keeps parent
`AG-BE-ID-003` blocked on the servant session type-contract decision.

This packet does not approve, reopen, or implement parent `AG-BE-ID-003`.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex` and read the central status root
configured by `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | active `in_progress`; owner `Codex`, reviewer `Codex2` | This packet is the support-only artifact for review. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | archived `done`; packet PR #1940 and closeout PR #1943 merged | Previous AG-BE-ID-003 handoff is durable and still says the parent is blocked. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Parent implementation must not proceed until the servant session type contract is decided. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` is the accepted upstream servant ensure/provision/reconcile surface. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and capability manifest remain present on `dev`. |
| `AG-XR-003` | `blocked`; owner `Codex`, reviewer `Claude2`, waiting for `Claude2` | Cross-repo compatibility/deployment gate remains unresolved. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13` | active `in_progress`; owner `Codex`, reviewer `Codex2` | Active support-only acceptance refresh; not merged parent compatibility evidence. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-20` | archived `done`; PR #1944 / merge `b3b5b1c3` | Previous frontend support packet is durable and continues to gate frontend session UI on AG-BE-ID-003. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | `review`; PR #1945 / merge `97cfbdd5` | Latest frontend support packet is review material, not backend/session runtime readiness. |
| `AG-FE-ID-001` | `todo`; depends on `AG-FE-000` and `AG-BE-ID-003` | Frontend parent implementation has not started in durable task state. |

Dependency honesty rule: the frontend may continue to use identity and servant
profile readiness as support context, but it must not claim interactive,
trainer, or research-task session readiness while `AG-BE-ID-003` is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_11.md` | This task-scoped assignment and support-only boundary. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Confirms active task state, owner, reviewer, artifact, and support-only acceptance. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Confirms predecessor archived `done`, with PR #1940 and closeout PR #1943 merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent remains blocked on the servant session type-contract decision. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms servant ensure/provision/reconcile is archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI/capability manifest delivery is archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility manifest/deployment gate remains blocked. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13` | Confirms a newer AG-XR support refresh is active but not a merged parent resolution. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-20` | Confirms latest frontend support packet is archived `done` through PR #1944. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | Confirms latest frontend support packet is in `review`, with PR #1945 merged and no runtime/OpenAPI/frontend source delta. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms frontend parent remains `todo` and still depends on `AG-BE-ID-003`. |
| `git rev-parse origin/dev` and `git rev-parse HEAD` | Confirms this task branch was refreshed against current `origin/dev`, `97cfbdd5`. |
| `git log --oneline c009f0a5..origin/dev` | Shows only AG-FE-ID-001 followup-20 review/closeout material and followup-21 packet material after followup 10 closeout. |
| `git log --oneline c009f0a5..origin/dev -- ...` | No post-followup-10 commits touched the checked BFF/OpenAPI/Agora/spec/compatibility/AG-BE-ID-003/execute-plans Agora pathset. |
| `git diff --name-only c009f0a5..origin/dev -- ...` | Empty for the checked parent handoff pathset. |
| `git log --oneline c009f0a5..origin/dev -- support/sidecars/AG-FE-ID-001 ...` | Shows AG-FE-ID-001 followup-20 review/closeout support commits and followup-21 support packet after followup 10. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | `ServantSessionCreateRequest` still lacks a public session type field while route family exists on paper. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant router still owns `/servant/ensure`; no servant-session runtime routes are implemented there. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Compatibility status remains `pending` with frontend placeholder/type blocking reasons. |
| `services/control-plane/bff/main.py` | Legacy `/bff/agora/sessions*` routes and SSE alias remain in `main.py`; stream alias still ignores `sessionId`. |
| `/home/lupin/code/execute-plans` remote probes after `git fetch origin --prune` | Confirms `origin/main` and `origin/dev` target-file status is unchanged from followup-20. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup 10

| Change | What changed | Parent implication |
|---|---|---|
| Followup 10 closed | Archived `done`; packet PR #1940 and closeout PR #1943 are merged. | Treat followup 10 as accepted support evidence. |
| Dev advanced to `97cfbdd5` | AG-FE-ID-001 followup-20 review/closeout and followup-21 packet support material landed. | Additional frontend support context landed, but no BFF/runtime/contract/session implementation changed in the checked parent pathset. |
| Checked parent pathset | No diff from `c009f0a5..origin/dev` over BFF, OpenAPI, Agora specs, compatibility manifest, AG-BE-ID-003 support, or execute-plans Agora mirror. | No new evidence unblocks AG-BE-ID-003. |
| Frontend support state | AG-FE-ID-001 followup-20 is archived `done`; followup-21 is in `review`; parent `AG-FE-ID-001` remains `todo`. | Frontend remains downstream and must not enable servant-session UI before AG-BE-ID-003 lands. |
| Followup-20 temporal note | That FE packet was written while AG-BE-ID-003 followup-10 was in `review`; current status root now archives followup-10 as `done`. | This packet supersedes that narrow status detail but not the dependency gate. |
| Followup-21 temporal note | That FE packet says AG-BE-ID-003 followup-11 was active under `Antigravity` with no artifact. Current status root now has followup-11 owned by `Codex`, and this packet is the artifact. | This packet supersedes that narrow sidecar-status detail; it still does not unblock the parent. |
| Cross-repo compatibility | AG-XR-003 remains blocked; dev compatibility manifest remains `pending`. | Strict v1.1 release/readiness claims stay gated. |

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

AG-FE-ID-001 followup-20 and followup-21 are useful support evidence, but they
did not add servant-session BFF runtime behavior, OpenAPI changes, or
executable frontend session clients. They reinforce the downstream gate rather
than unblocking it.

## 6. Decision Options For Parent Reviewer

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred because it is least ambiguous. |
| Add an explicit equivalent such as `session_kind` | Same result if the field is documented and mapped to OpenClaw. | Acceptable if reviewer names the field. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Acceptable only with a deterministic, documented derivation rule. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract unless explicitly promoted by reviewer. |
| Default all creates to one type | Quick to code. | Should stay blocked; it fails parent acceptance for three visible types. |

## 7. Current Route Evidence

| Surface | Current observation at dev `97cfbdd5` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, get, messages, terminate, and stream. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks a session type field. | Blocks strict create UI and parent implementation review. |
| v1.1 capability manifest | `AG-XR-OPENAPI-001` archived delivery declares `agora.servant.v1` with `/bff/agora/servant`. | Correct discovery layer, not runtime proof. |
| Dev compatibility manifest | `compatibility_status` remains `pending`; frontend commit/type placeholders remain in blocking reasons. | Strict live frontend remains gated. |
| Servant router | Implements servant ensure/provision/reconcile behavior only. | AG-BE-ID-003 must compose with this route and not overwrite AG-BE-ID-002 behavior. |
| BFF runtime | No BFF implementation for `/bff/agora/servant/sessions` was found in checked runtime paths. | Parent still needs implementation after the type decision. |
| Legacy `/bff/agora/sessions` | Existing `main.py` route creates legacy ask/session records, accepting `mode` or `sessionType` and defaulting to `quick_ask`. | Not a safe substitute for servant sessions. |
| Legacy SSE alias | `GET /bff/sse/agora/sessions/{sessionId}` delegates to `stream_ask_events()` and does not use `sessionId`. | Not proof of servant session-scoped SSE. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` was not found in checked BFF runtime paths. | Parent must preserve accepted degradation semantics or get reviewer approval for an explicit mapping. |
| Frontend sidecar state | AG-FE-ID-001 followup-20 is done and followup-21 is in review, but parent `AG-FE-ID-001` remains `todo` and depends on AG-BE-ID-003. | No frontend strict-live enablement yet. |

## 8. Frontend Handoff

Until the parent records the type-contract decision and lands the runtime route
family, execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

Remote probe source: `/home/lupin/code/execute-plans` after
`git fetch origin --prune`, checking `origin/main` at
`7b2f17c4dee8dcafe62c2295504df03aed0ae16e` and `origin/dev` at
`7aa4917272212452fe5e4dc99bf2d76fe48eacfd`. `origin/HEAD` points to
`origin/main`; the local worktree remains `main...origin/main [ahead 2, behind
467]`, so parent implementation should use remote tree checks or a clean
frontend task worktree.

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Display capability readiness | `GET /bff/agora/capabilities` or capability manifest context | Discovery/readiness only; not session runtime proof. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged. |
| Use AG-FE-ID-001 support packets as planning inputs | AG-FE-ID-001 support artifacts through followup-21 | Handoff context only; not executable frontend proof. |

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

### Remote target-file status

| Surface | `origin/main` | `origin/dev` | Handoff rule |
|---|---|---|---|
| `src/agora/AgoraApp.tsx` | Missing | Missing | Parent must add the shell or block for missing design/spec authority. |
| `src/lib/bff-v1/agora/identity.ts` | Missing | Missing | Parent should add strict clients for `/me` and `/capabilities`. |
| `src/lib/bff-v1/agora/servant.ts` | Missing | Missing | Parent should add strict ensure client for `/servant/ensure` only. |
| `src/lib/bff-v1/agora/types.ts` | Missing | Present | Parent must confirm delivery branch and `AG-XR-003` disposition before relying on generated types. |
| `src/entries/agora-main.tsx` | Missing | Missing | Parent must resolve frontend delivery-base truth before claiming an Agora entry exists. |
| `vite.agora.config.ts` | Missing | Missing | Parent must not assume separate Agora Vite config is visible on checked remotes. |
| `agora.html` | Missing | Missing | Parent must verify the delivery base before depending on a separate Agora HTML entry. |
| `src/agora/pages/AskPersonas.tsx` | Present | Present | Ask/session UI must remain gated behind identity/servant readiness and the AG-BE-ID-003 session decision. |
| `src/lib/bff/agora.ts` | Present | Present | Not sufficient for parent acceptance; strict clients under `src/lib/bff-v1/agora/*` are still needed. |

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
3. delta assessment after followup 10 is accurate
4. AG-FE-ID-001 followup-20 and followup-21 are treated as downstream support context, not runtime readiness
5. frontend/operator gates remain conservative
6. parent absorption gates are actionable and do not implement canonical truth

## 12. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-13
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-20
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-ID-001
AI_NAME=Codex ./scripts/ai-status.sh progress AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11 "Read task-scoped context and predecessor packets; preparing support-only followup-11 BFF/frontend handoff refresh."
git rev-parse origin/dev
git rev-parse HEAD
git log --oneline c009f0a5774a81af0686b3a6e4eda21881918e0e..origin/dev
git log --oneline c009f0a5774a81af0686b3a6e4eda21881918e0e..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora
git diff --name-only c009f0a5774a81af0686b3a6e4eda21881918e0e..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora
git log --oneline c009f0a5774a81af0686b3a6e4eda21881918e0e..origin/dev -- support/sidecars/AG-FE-ID-001 .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_20.md .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_21.md
git diff --name-only c009f0a5774a81af0686b3a6e4eda21881918e0e..origin/dev -- support/sidecars/AG-FE-ID-001 .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_20.md .orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_21.md
rg -n "OPENCLAW_UPSTREAM_DEGRADED|servant/sessions|session_type|sessionType|ServantSessionCreateRequest" services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora
rg -n "stream_ask_events|/bff/sse/agora/sessions|/bff/agora/sessions|terminate" services/control-plane/bff/main.py
rg -n "servant/sessions|sessions|@router|ensure|reconcile" services/control-plane/bff/agora/servant/router.py
rg --files services/control-plane/bff/agora
sed -n '188,224p' services/control-plane/openapi/agora_v1_1.openapi.yaml
sed -n '624,726p' services/control-plane/openapi/agora_v1_1.openapi.yaml
sed -n '1,220p' services/control-plane/bff/agora/servant/router.py
sed -n '1,200p' docs/contracts/agora/dev-compatibility-manifest.json
sed -n '20700,20860p' services/control-plane/bff/main.py
sed -n '42688,42708p' services/control-plane/bff/main.py
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans status -sb
git -C /home/lupin/code/execute-plans rev-parse origin/main
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans symbolic-ref refs/remotes/origin/HEAD
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/main src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts package.json
git -C /home/lupin/code/execute-plans ls-tree -r --name-only origin/dev src/agora/AgoraApp.tsx src/lib/bff-v1/agora/identity.ts src/lib/bff-v1/agora/servant.ts src/lib/bff-v1/agora/types.ts src/entries/agora-main.tsx vite.agora.config.ts agora.html src/agora/pages/AskPersonas.tsx src/lib/bff/agora.ts package.json
```

No runtime tests were run because this sidecar changes only support artifacts.

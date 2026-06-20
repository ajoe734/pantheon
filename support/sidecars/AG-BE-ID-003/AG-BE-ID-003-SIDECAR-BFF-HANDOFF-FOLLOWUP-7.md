# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 7

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Date | `2026-06-20` |
| Status | `review_ready` |
| Current dev base | `c0af1ff82dbaf0c1e039fff2ced33304f06cc225` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, or execute-plans source
files.

## 1. Purpose

Followup 6 is archived done through closeout PR #1919. Current `origin/dev` for
this followup is `c0af1ff82dbaf0c1e039fff2ced33304f06cc225`. Since followup 6,
a focused diff of the BFF, OpenAPI, Agora spec, capability manifest, dispatch,
and AG-BE-ID-003 support paths shows zero implementation changes: no new commits
touched `services/control-plane/openapi/`, `services/control-plane/bff/`,
`services/control-plane/specs/agora/`, or the `AG-BE-ID-003` support sidecar
directory after the followup-6 merge. The dev log shows only sidecar/acceptance
PR merges for AG-XR-003 followup-11, AG-FE-DB-002 followup-8, and
AG-FE-ID-001 followup-15 advancing `origin/dev` from `f49e257c` to `c0af1ff8`.

This packet therefore carries no new implementation delta. It confirms the
unchanged blocker state for the parent owner and keeps the frontend/operator
gate picture current.

This packet does not approve, reopen, or implement the parent task.

## 2. Sources Checked

| Source | Why it matters |
|---|---|
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-ID-003` | Parent remains `blocked`; same type-contract blocker. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-ID-002` | Upstream servant ensure/provision/reconcile is archived done. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Confirms followup 6 is archived done (PR #1919) and provides the prior closeout baseline. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-003` | Confirmed still `blocked`; execute-plans PR #63 integration gate still fails (frontend generated Agora types remain v1 / not v1.1). |
| `git diff --name-only f49e257c..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/04/pantheon_agora_cross_repo_2026-06-20 support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora` | No files in these paths changed since followup-6 base. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | `ServantSessionCreateRequest` still defines only `intent`, `strategy_ref`, and `metadata`; `additionalProperties: false`. No `session_type` field. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | `compatibility_status` is still `pending`. |
| `services/control-plane/bff/` (grep scan) | No `OPENCLAW_UPSTREAM_DEGRADED` or `servant/sessions` implementation found in BFF code. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 manifest still declares `agora.servant.v1` with `/bff/agora/servant` — unchanged since followup-6. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 3. Current Parent State

`AG-BE-ID-002` is complete. Its archived delivery includes `POST /bff/agora/servant/ensure`
as the upstream servant profile/provisioning surface. AG-BE-ID-003 can compose
with this endpoint.

`AG-XR-OPENAPI-001` is archived done. The v1.1 OpenAPI route family and v1.1
capability manifest are present on dev.

`AG-XR-003` remains blocked. Execute-plans PR #63 integration gate fails because
frontend generated Agora types remain v1 and not v1.1-ready. No change since
followup-6 on this surface.

`AG-BE-ID-003` remains blocked. The parent's own status note reads:

> Blocked before implementation: AG-BE-ID-003 requires POST
> /bff/agora/servant/sessions for interactive/trainer/research_task, but
> canonical ServantSessionCreateRequest only allows intent/strategy_ref/metadata
> with additionalProperties=false and no session_type field. Design-closure C1
> common envelope has session_type and strategy-dialogue allows
> interactive/trainer, but no canonical BFF request contract says where the
> client supplies or how BFF derives research_task. Need reviewer decision.

This sidecar does not resolve that blocker. The parent owner (`Codex2`) and
reviewer (`Claude`) must record the type-contract decision before implementation
can proceed.

## 4. Contract Decision Request (unchanged from followup 6)

### D1 - Public create schema has no type field

`ServantSessionCreateRequest` in `agora_v1_1.openapi.yaml` allows only:

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

No `session_type`, `sessionType`, or equivalent top-level field is present.
Because `additionalProperties` is `false`, a strict frontend cannot send either
field at the top level.

### D2 - OpenClaw invocation envelope needs a type

The design-closure common OpenClaw skill envelope includes:

```json
"session_type": "interactive|trainer|research_task|consult|committee|red_team|background_job"
```

AG-BE-ID-003 requires a frozen mapping from the public BFF request to the
OpenClaw session invocation type before implementation can be reviewed.

### D3 - Research task mapping is not identified by the checked skill spec

The checked `agora-strategy-dialogue` skill allows only `interactive` and
`trainer`. The parent owner must name the skill/session kind that owns
`research_task` sessions, or obtain reviewer-approved scope change before coding.

### D4 - Discovery split across v1, v1.1, and compatibility layers

The frozen v1 `capability_manifest.json` still lists legacy prefixes such as
`/bff/agora/sessions` and `/bff/agora/ask/sessions`. The v1.1 manifest correctly
declares `agora.servant.v1` with `/bff/agora/servant`, but the
`dev-compatibility-manifest.json` remains `pending`. Strict live frontend should
stay gated until compatibility status resolves.

## 5. Decision Options For Parent Reviewer (unchanged from followup 6)

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract-compliant clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred, lowest ambiguity. |
| Add an explicit equivalent such as `session_kind` | Same as above if the field is documented, required or deliberately optional, and mapped to OpenClaw. | Acceptable if named by reviewer. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Risky unless the derivation rule is deterministic and documented. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract; not strict-mode ready unless explicitly approved as public contract. |
| Default all creates to one type | Quick to implement. | Should remain blocked; it conflicts with parent acceptance for three visible types. |

## 6. Current Route Evidence

| Surface | Current observation at dev `c0af1ff8` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, `GET`, `messages`, `terminate`, and `stream`. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks session type. | Blocks strict create UI and parent implementation review. |
| v1 capability manifest | Frozen v1 still lists legacy session prefixes, not `/bff/agora/servant`. | Not enough for servant-session discovery. |
| v1.1 capability manifest | Declares `agora.servant.v1` with `/bff/agora/servant`. | Correct contract discovery layer, but still not runtime proof. |
| Dev compatibility manifest | `compatibility_status` is `pending`. | Strict live frontend should stay gated. |
| execute-plans mirror | Lists servant session operation IDs and `/bff/agora/servant/sessions*` paths; no typed runtime client or `session_type` in public request contract. | Metadata only; not deployment-ready proof. |
| Agora package router | No servant session router is registered. | BFF implementation absent. |
| Servant router | Implements servant ensure/provision/reconcile behavior (AG-BE-ID-002). | AG-BE-ID-003 must compose with, not overwrite, this. |
| BFF runtime | No BFF implementation for `servant/sessions` found. | Parent still needs implementation after decision. |
| Legacy `/bff/agora/sessions` | `main.py` and `read_store.py` still default mode to `quick_ask`. | Not a safe substitute for servant sessions. |
| Legacy SSE | `GET /bff/sse/agora/sessions/{sessionId}` delegates to ask-session stream. | Not proof of servant session-scoped SSE. |
| Degraded error | BFF enum and handlers use `DEPENDENCY_UNAVAILABLE`; no `OPENCLAW_UPSTREAM_DEGRADED` found. | Parent must preserve the accepted degradation code or get reviewer approval for envelope mapping. |

## 7. Frontend Handoff

Until the parent records the type-contract decision and lands the route family,
execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged. |
| Display v1.1 capability hints | `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` or mirrored metadata | Discovery only; compatibility manifest is still pending and runtime sessions are absent. |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive servant session | No public create contract field or derivation rule for `interactive`. |
| Create trainer servant session | Same type blocker, though strategy-dialogue allows trainer. |
| Create research-task servant session | No named `research_task` skill/session mapping in checked skill spec. |
| Send servant session message | OpenAPI path exists, but BFF implementation is absent. |
| Terminate servant session | OpenAPI path exists, but BFF implementation is absent. |
| Stream servant session events | OpenAPI path exists at `/stream`, but BFF implementation is absent and legacy SSE is shared ask-channel. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` is not in current BFF enum/spec search results. |
| Claim strict v1.1 cross-repo compatibility | `AG-XR-003` and `dev-compatibility-manifest.json` remain pending/blocked. |

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

## 8. Operator Journey

### Before parent decision

1. Operator resolves Agora identity through `GET /bff/agora/me`.
2. Operator ensures the private servant through `POST /bff/agora/servant/ensure`.
3. UI shows servant readiness and v1.1 capability hints.
4. Session create/message/stream/terminate controls stay disabled with a
   backend-contract-unavailable state.

### After parent implementation

1. Operator resolves identity and ensures servant readiness.
2. Operator creates a servant session with the parent-approved representation of
   `interactive`, `trainer`, or `research_task`.
3. BFF records `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`,
   and `session_id` in response meta and audit trail.
4. BFF maps the session to the approved OpenClaw skill/session kind.
5. Operator sends messages; BFF records idempotency and dispatches work to the
   OpenClaw-backed session.
6. Frontend receives only events for that servant session through the scoped SSE
   route.
7. OpenClaw degradation returns a 503 preserving `OPENCLAW_UPSTREAM_DEGRADED` or
   an explicitly approved equivalent envelope.
8. Operator terminates the session; BFF persists terminal status and emits a
   terminal event.

## 9. Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| P0 upstream servant | Compose with merged `POST /bff/agora/servant/ensure` from AG-BE-ID-002. |
| P1 type contract | Record how the create request carries or derives `interactive`, `trainer`, and `research_task`. |
| P2 OpenAPI/schema alignment | Update or explicitly approve the public create contract; do not accept undeclared top-level fields. |
| P3 research mapping | Name the OpenClaw skill/session kind that owns `research_task`. |
| P4 package placement | Add servant-session logic without overwriting AG-BE-ID-002 ensure behavior. Prefer a servant/session package boundary over more `main.py` logic. |
| P5 discovery source | Use v1.1 capability manifest or an explicit compatibility rule for servant sessions; do not rely on frozen v1 prefixes alone. |
| P6 cross-repo compatibility | Keep strict live FE gated until `AG-XR-003` compatibility status is compatible or reviewer/ops records an explicit disposition. |
| P7 audit fields | Include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` for reads/writes and response meta. |
| P8 degradation code | Preserve `OPENCLAW_UPSTREAM_DEGRADED` as the accepted session degradation code or get reviewer approval for a precise mapping. |
| P9 SSE scope | Implement servant session stream scoped by `session_id`; do not reuse the shared ask-channel stream as proof. |
| P10 legacy route policy | State whether legacy `/bff/agora/sessions` remains compatibility-only, becomes an alias, or is out of scope. |
| P11 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, capability discovery, compatibility gating, and degradation. |

## 10. Review Ask

Claude2 should review only the sidecar packet boundary and factual handoff:

1. support-only scope is preserved
2. parent blocker restatement matches current task state
3. delta assessment (zero BFF/Agora implementation change since followup-6) is accurate
4. frontend/operator gates remain conservative
5. parent absorption gates are actionable and do not implement canonical truth

## 11. Verification Run

Commands run for this packet:

```bash
git branch --show-current
git status --short
git rev-parse origin/dev
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-ID-003
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-ID-002
AI_NAME=Claude python3 scripts/ai_status.py show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Claude python3 scripts/ai_status.py show AG-XR-003
git diff --name-only f49e257c570252964191212af8c2fe915e1e8535..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/04/pantheon_agora_cross_repo_2026-06-20 support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora
git log --oneline f49e257c570252964191212af8c2fe915e1e8535..origin/dev -- services/control-plane/openapi/ services/control-plane/bff/ services/control-plane/specs/agora/
grep -A 20 "ServantSessionCreateRequest:" services/control-plane/openapi/agora_v1_1.openapi.yaml
cat docs/contracts/agora/dev-compatibility-manifest.json
grep -rn "OPENCLAW_UPSTREAM_DEGRADED\|servant/sessions\|session_type" services/control-plane/bff/
```

No runtime tests were run because this sidecar changes only support artifacts.

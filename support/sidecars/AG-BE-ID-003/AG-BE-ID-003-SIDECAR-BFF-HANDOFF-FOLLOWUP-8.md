# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 8

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `review_ready` |
| Current dev base | `d11a6fc98dc4c10678c17c26fdaba68394dfedd6` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, or execute-plans source
files.

## 1. Purpose

Followup 7 is archived done through PR #1924. This followup starts from the
same dev merge commit, `d11a6fc98dc4c10678c17c26fdaba68394dfedd6`.
After fetching `origin/dev`, there is no new diff after followup 7 on the
checked BFF, OpenAPI, Agora spec, compatibility manifest, or AG-BE-ID-003
support paths. `HEAD` also equals `origin/dev`.

This packet therefore carries no new implementation delta. It refreshes the
parent and frontend handoff with the current status-script evidence and keeps
the parent blocked until the reviewer records the servant session type-contract
decision.

This packet does not approve, reopen, or implement the parent task.

## 2. Sources Checked

| Source | Why it matters |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | Confirms this sidecar is active `in_progress`, owned by `Codex`, reviewed by `Codex2`, and scoped to this support packet. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Parent remains `blocked`, waiting for `Claude` on the servant session type contract. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Confirms predecessor is archived done, with PR #1924 merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Confirms upstream servant ensure/provision/reconcile is archived done and can be composed with. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Confirms cross-repo compatibility remains blocked at the execute-plans integration gate. |
| `git fetch origin dev` and `git rev-parse origin/dev` | Confirms current dev base is still `d11a6fc98dc4c10678c17c26fdaba68394dfedd6`. |
| `git diff --name-only d11a6fc98dc4c10678c17c26fdaba68394dfedd6..origin/dev -- ...` | No BFF, OpenAPI, Agora spec, compatibility manifest, execute-plans mirror, or AG-BE-ID-003 support path changed after followup 7. |
| `git log --oneline d11a6fc98dc4c10678c17c26fdaba68394dfedd6..origin/dev -- ...` | No post-followup-7 commits touched the checked paths. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | `ServantSessionCreateRequest` still allows only `intent`, `strategy_ref`, and `metadata`; `additionalProperties: false`. |
| `services/control-plane/bff/agora/servant/router.py` | Servant router still owns `POST /bff/agora/servant/ensure`; it does not implement servant sessions. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 manifest still declares `agora.servant.v1` with `/bff/agora/servant`. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | `compatibility_status` is still `pending`; blocking reasons still include frontend placeholder/generated-type gaps. |
| `rg -n "OPENCLAW_UPSTREAM_DEGRADED\|servant/sessions\|session_type\|sessionType\|ServantSessionCreateRequest" ...` | Finds servant session paths only in OpenAPI; no BFF runtime servant-session implementation is present. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 3. Current Parent State

`AG-BE-ID-002` is complete. Its archived delivery includes the upstream servant
ensure/provision/reconcile surface. `AG-BE-ID-003` must compose with that
servant profile behavior and must not overwrite it.

`AG-XR-OPENAPI-001` and the v1.1 OpenAPI artifacts are already on dev.
The route family for servant sessions exists on paper, but the runtime BFF
implementation is still absent.

`AG-XR-003` remains blocked. The dev compatibility manifest is still pending,
and the status note still points at execute-plans PR #63 failing the aggregate
release gate because frontend generated Agora types remain v1 rather than
v1.1-ready.

`AG-BE-ID-003` remains blocked. The active parent status says:

> Blocked before implementation: AG-BE-ID-003 requires POST
> /bff/agora/servant/sessions for interactive/trainer/research_task, but
> canonical services/control-plane/openapi/agora_v1_1.openapi.yaml
> ServantSessionCreateRequest only allows intent/strategy_ref/metadata with
> additionalProperties=false and no session_type field. Design-closure C1 common
> envelope has session_type and strategy-dialogue allows interactive/trainer,
> but no canonical BFF request contract says where the client supplies or how
> BFF derives research_task. Need reviewer decision: add/approve session_type
> in the BFF create schema, derive session_type server-side from route/context,
> or constrain AG-BE-ID-003 to a default session_type. I did not modify
> implementation files.

This sidecar does not resolve that blocker. Parent owner `Codex2` and reviewer
`Claude` must record the type-contract decision before implementation can
proceed.

## 4. Contract Decision Request

### D1 - Public create schema still has no type field

`ServantSessionCreateRequest` still has only:

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
Because `additionalProperties` is `false`, strict clients cannot send an
undeclared top-level type field.

### D2 - OpenClaw session invocation still needs a type

The BFF-to-OpenClaw session creation path needs a deterministic session type
for `interactive`, `trainer`, and `research_task`. AG-BE-ID-003 cannot safely
invent the mapping from public BFF request to OpenClaw session lifecycle during
implementation.

### D3 - Research-task mapping remains unresolved

Existing checked evidence still identifies `interactive` and `trainer` in the
strategy-dialogue path. The parent needs a named skill/session kind for
`research_task`, or an explicit reviewer-approved scope change.

### D4 - Discovery and compatibility remain split

The v1.1 manifest advertises `/bff/agora/servant`, but the dev compatibility
manifest is still `pending`. Strict live frontend should remain gated until
compatibility is compatible or reviewer/ops records a disposition.

## 5. Decision Options For Parent Reviewer

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred because it is least ambiguous. |
| Add an explicit equivalent such as `session_kind` | Same as above if documented and mapped to OpenClaw. | Acceptable if reviewer names the field. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Acceptable only with a deterministic, documented derivation rule. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract unless explicitly promoted by reviewer. |
| Default all creates to one type | Quick to code. | Should stay blocked; it fails parent acceptance for three visible types. |

## 6. Current Route Evidence

| Surface | Current observation at dev `d11a6fc` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, session get, messages, terminate, and stream. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks a session type field. | Blocks strict create UI and implementation review. |
| v1.1 capability manifest | Declares `agora.servant.v1` with `/bff/agora/servant`. | Correct discovery layer, not runtime proof. |
| Dev compatibility manifest | `compatibility_status` is `pending`; frontend commit/type placeholders remain in blocking reasons. | Strict live frontend remains gated. |
| Servant router | Implements servant ensure/provision/reconcile behavior only. | AG-BE-ID-003 must compose with this route. |
| BFF runtime | No BFF implementation for `/bff/agora/servant/sessions` was found. | Parent still needs implementation after the decision. |
| Legacy `/bff/agora/sessions` | Existing main/read-store paths still use legacy session mode behavior such as `quick_ask`. | Not a safe substitute for servant sessions. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` was not found in the checked BFF runtime paths. | Parent must preserve or explicitly map the accepted degradation code. |

## 7. Frontend Handoff

Until the parent records the type-contract decision and lands the route family,
execute-plans should keep servant session create/message/stream/terminate
controls disabled in strict live mode.

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged. |
| Display v1.1 capability hints | v1.1 capability manifest or mirrored metadata | Discovery only; compatibility is still pending and runtime sessions are absent. |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive servant session | No public create contract field or derivation rule for `interactive`. |
| Create trainer servant session | Same type blocker, though trainer appears in existing strategy/training surfaces. |
| Create research-task servant session | No named `research_task` skill/session mapping in checked evidence. |
| Send servant session message | OpenAPI path exists, but BFF implementation is absent. |
| Terminate servant session | OpenAPI path exists, but BFF implementation is absent. |
| Stream servant session events | OpenAPI path exists, but BFF implementation is absent and legacy SSE is not proof of servant-session scope. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` was not found in the checked BFF runtime paths. |
| Claim strict v1.1 cross-repo compatibility | `AG-XR-003` and `dev-compatibility-manifest.json` remain blocked/pending. |

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
3. UI may show servant readiness and v1.1 capability hints.
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
7. OpenClaw degradation returns a 503 preserving `OPENCLAW_UPSTREAM_DEGRADED`
   or an explicitly approved equivalent envelope.
8. Operator terminates the session; BFF persists terminal status and emits a
   terminal event.

## 9. Parent Absorption Gates

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
| P11 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, capability discovery, compatibility gating, and degradation. |

## 10. Review Ask

Codex2 should review only the sidecar packet boundary and factual handoff:

1. support-only scope is preserved
2. parent blocker restatement matches current task state
3. delta assessment after followup 7 is accurate
4. frontend/operator gates remain conservative
5. parent absorption gates are actionable and do not implement canonical truth

## 11. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-8
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003
git fetch origin dev
git rev-parse origin/dev
git rev-parse HEAD
git log --oneline d11a6fc98dc4c10678c17c26fdaba68394dfedd6..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003
git diff --name-only d11a6fc98dc4c10678c17c26fdaba68394dfedd6..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora support/sidecars/AG-BE-ID-003 execute-plans/src/lib/bff-v1/agora
rg --files services/control-plane/bff/agora
rg -n "OPENCLAW_UPSTREAM_DEGRADED|servant/sessions|session_type|sessionType|ServantSessionCreateRequest" services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora
sed -n '188,218p' services/control-plane/openapi/agora_v1_1.openapi.yaml
sed -n '1,220p' services/control-plane/bff/agora/servant/router.py
sed -n '1,180p' services/control-plane/specs/agora/v2/capability_manifest_v1_1.json
sed -n '1,160p' docs/contracts/agora/dev-compatibility-manifest.json
```

No runtime tests were run because this sidecar changes only support artifacts.

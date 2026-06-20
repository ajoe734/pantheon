# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 6

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `review_ready` |
| Current dev base | `f49e257c570252964191212af8c2fe915e1e8535` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, or execute-plans source
files.

## 1. Purpose

Followup 5 is archived done through closeout PR #1904 at merge
`80f2832373aa390a952d61022b50933a473171ca`. Current `origin/dev` for this
followup is `f49e257c570252964191212af8c2fe915e1e8535`. Since followup 5,
focused dev delta shows only `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8.md`
changed; no AG-BE-ID-003 BFF, OpenAPI, Agora spec, capability manifest, dispatch,
or AG-BE-ID-003 support file changed after the followup-5 closeout base.

This packet refreshes the parent handoff with one material nuance:

1. `AG-XR-OPENAPI-001` is archived done, and the v1.1 capability manifest now
   declares `agora.servant.v1` with prefix `/bff/agora/servant`
2. `AG-XR-003` remains blocked/pending for cross-repo compatibility because the
   frontend generated type/runtime manifest is not a compatible v1.1 deployment
   proof
3. the core AG-BE-ID-003 blocker is unchanged: the servant session create schema
   still has no public `session_type` or approved derivation rule

This packet does not approve, reopen, or implement the parent task.

## 2. Sources Checked

| Source | Why it matters |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Parent remains active and blocked waiting for `Claude` to decide the servant-session type contract. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Upstream servant ensure/provision/reconcile is archived done and merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirms followup 5 is archived done and gives the prior closeout baseline. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI and capability manifest work is archived done. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003` | Confirms dev compatibility manifest work remains blocked/pending on frontend v1.1 generation/release gate. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` | Immediate predecessor packet and decision matrix. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | Defines `/bff/agora/servant/sessions` route family and incomplete create schema. |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen v1 manifest still lists legacy session prefixes, not `/bff/agora/servant`. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 manifest declares `agora.servant.v1` and `/bff/agora/servant`. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Current compatibility status is `pending`; blockers cite frontend generated type/runtime placeholders. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Repo mirror lists servant session operation IDs, but does not add a typed public `session_type` create contract or runtime client. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md` | Common OpenClaw skill envelope includes `session_type`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-dialogue/SPEC.md` | Strategy dialogue allows `interactive` and `trainer`, not `research_task`. |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent acceptance still requires interactive/trainer/research sessions, SSE, audit fields, and `OPENCLAW_UPSTREAM_DEGRADED`. |
| `services/control-plane/bff/agora/router.py` and `services/control-plane/bff/agora/servant/router.py` | Confirms the package router registers servant ensure only; no servant-session router is included. |
| `services/control-plane/bff/main.py` and `services/control-plane/bff/read_store.py` | Legacy sessions still default/filter on `quick_ask`; legacy SSE alias is not session-scoped. |
| `services/control-plane/bff/models.py` | Current BFF error enum has `DEPENDENCY_UNAVAILABLE`, not `OPENCLAW_UPSTREAM_DEGRADED`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 3. Current Parent State

`AG-BE-ID-002` is complete. Its archived delivery records merge target
`247211c2208d15bce628c017044a3bf2062603e6`, and AG-BE-ID-003 can compose with
`POST /bff/agora/servant/ensure` as the upstream servant profile/provisioning
surface.

`AG-XR-OPENAPI-001` is also archived done. The v1.1 OpenAPI route family and
v1.1 capability manifest are present on dev. That improves discovery context,
but it does not remove the parent blocker because the public create request
contract still cannot carry the required servant session type.

`AG-XR-003` remains blocked/pending. The dev compatibility manifest includes
`agora.servant.v1`, but `compatibility_status` is `pending` with blockers for
frontend generated contract commit/runtime placeholders and frontend generated
types not being a deployment-ready Agora v1.1 proof.

`AG-BE-ID-003` remains blocked before implementation. Its active status says the
task requires `POST /bff/agora/servant/sessions` for `interactive`, `trainer`,
and `research_task`, but the canonical BFF create schema does not identify where
a compliant client supplies that type or how the BFF derives it.

The parent blocker is still a contract decision, not a coding gap the owner
should fill by guesswork.

## 4. Contract Decision Request

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

The schema has no `session_type`, `sessionType`, or equivalent top-level field.
Because `additionalProperties` is `false`, a strict frontend cannot send either
field in the top-level request.

### D2 - OpenClaw invocation envelope needs a type

The design-closure common OpenClaw skill envelope includes:

```json
"session_type": "interactive|trainer|research_task|consult|committee|red_team|background_job"
```

AG-BE-ID-003 therefore needs a frozen mapping from public BFF request to
OpenClaw session invocation before implementation can be reviewed against the
spec.

### D3 - Research task mapping is not identified by the checked skill spec

The checked `agora-strategy-dialogue` skill allows only:

```text
interactive, trainer
```

That covers two parent-required types but not `research_task`. The parent owner
must name the skill/session kind that owns research-task sessions or get a
reviewer-approved scope change before coding.

### D4 - Discovery is split across frozen v1, v1.1, and deployment compatibility

The frozen v1 `capability_manifest.json` still lists legacy prefixes such as
`/bff/agora/sessions` and `/bff/agora/ask/sessions`.

The v1.1 `capability_manifest_v1_1.json` now correctly declares:

```json
"name": "agora.servant.v1",
"bff_path_prefixes": ["/bff/agora/servant"]
```

That is useful for parent implementation and frontend planning, but it is not
enough to enable strict live UI. `docs/contracts/agora/dev-compatibility-manifest.json`
is still `pending`, and the execute-plans mirror contains operation metadata
without a typed runtime client or a public create request field for
`session_type`.

## 5. Decision Options For Parent Reviewer

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract-compliant clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred, lowest ambiguity. |
| Add an explicit equivalent such as `session_kind` | Same as above if the field is documented, required or deliberately optional, and mapped to OpenClaw. | Acceptable if named by reviewer. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Risky unless the derivation rule is deterministic and documented. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract; not strict-mode ready unless explicitly approved as public contract. |
| Default all creates to one type | Quick to implement. | Should remain blocked; it conflicts with parent acceptance for three visible types. |

## 6. Current Route Evidence

| Surface | Current observation at dev `f49e257c` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, `GET`, `messages`, `terminate`, and `stream`. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks session type. | Blocks strict create UI and parent implementation review. |
| v1 capability manifest | Frozen v1 still lists legacy session prefixes, not `/bff/agora/servant`. | Not enough for servant-session discovery. |
| v1.1 capability manifest | Declares `agora.servant.v1` with `/bff/agora/servant`. | Correct contract discovery layer, but still not runtime proof. |
| Dev compatibility manifest | `compatibility_status` is `pending`; blockers include frontend generated contract/runtime placeholders and frontend generated types not v1.1-ready. | Strict live frontend should stay gated. |
| execute-plans mirror | `types.ts` lists servant session operation IDs and `/bff/agora/servant/sessions*` paths. | Useful metadata, but no `identity.ts`/`servant.ts` runtime client and no typed request contract solving `session_type`. |
| Agora package router | Includes identity, servant, strategy workshop, research, trading room, dashboard, shadow, personalization, and management projection routers. | No servant session router is registered. |
| Servant router | Implements servant ensure/provision/reconcile behavior. | AG-BE-ID-003 must compose with, not overwrite, AG-BE-ID-002 ensure behavior. |
| BFF runtime | Focused search found no BFF implementation for `servant/sessions`. | Parent still needs implementation after decision. |
| Legacy `/bff/agora/sessions` | `main.py` and `read_store.py` still default mode to `quick_ask`. | Not a safe substitute for servant sessions. |
| Legacy `/bff/agora/ask/sessions` | Hardcodes and filters `mode == "quick_ask"`. | Covers ask sessions only. |
| Legacy SSE | `GET /bff/sse/agora/sessions/{sessionId}` delegates to `stream_ask_events()` and ignores `sessionId` for filtering. | Not proof of session-scoped servant SSE. |
| Degraded error | BFF enum and handlers use `DEPENDENCY_UNAVAILABLE`; no `OPENCLAW_UPSTREAM_DEGRADED` match was found. | Parent must preserve the accepted degradation code or get reviewer approval for an envelope mapping. |

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

The wire field must match the parent-approved OpenAPI/schema field exactly. The
frontend should not use undeclared top-level fields or `metadata.session_type`
as a workaround unless the parent reviewer explicitly makes that the contract.

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
7. OpenClaw degradation returns a 503 preserving
   `OPENCLAW_UPSTREAM_DEGRADED` or an explicitly approved equivalent envelope.
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

Codex2 should review only the sidecar packet boundary and factual handoff:

1. support-only scope is preserved
2. parent blocker restatement matches current task state
3. v1.1 capability/discovery nuance is accurate
4. frontend/operator gates remain conservative
5. parent absorption gates are actionable and do not implement canonical truth

## 11. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
./scripts/git/task_start.sh "AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6"
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-003
git diff --name-only 80f2832373aa390a952d61022b50933a473171ca..origin/dev
git diff --stat 80f2832373aa390a952d61022b50933a473171ca..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/04/pantheon_agora_cross_repo_2026-06-20 scripts/dispatch_agora_cross_repo_2026-06-20.py support/sidecars/AG-BE-ID-003 .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_6.md
rg -n "ServantSessionCreateRequest|/bff/agora/servant/sessions|ServantSession" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/openapi/agora_v1.openapi.yaml
rg -n "servant/sessions|ServantSession|createServantSession|session_type|sessionType|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/bff services/control-plane/specs/agora/capability_manifest.json
rg -n "session_type|interactive|trainer|research_task|OPENCLAW_UPSTREAM_DEGRADED|AG-BE-ID-003" docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure scripts/dispatch_agora_cross_repo_2026-06-20.py
rg -n "servant/sessions|servant|sessions|agora\\.servant|agora\\.session" services/control-plane/specs/agora
rg -n "ServantSession|createServantSession|servant/sessions|session_type|sessionType" execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/agora/contract-snapshot.json execute-plans/src/lib/bff-v1/agora
```

Validation to run before handoff commit:

```bash
git diff --check -- .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_6.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
```

No runtime tests were run because this sidecar changes only support artifacts.

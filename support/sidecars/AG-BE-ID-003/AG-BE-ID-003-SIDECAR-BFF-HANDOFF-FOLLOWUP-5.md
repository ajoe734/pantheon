# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 5

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `review_approved` |
| Current dev base | `db2254d3984b5e719dfc0d433048e74176bfa068` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, or execute-plans source
files.

## 1. Purpose

Followup 4 is now archived done through closeout PR #1897 at merge
`e51bc8fdcdce119bd66596367c468364d18bf835`. Current `origin/dev` for closeout
is `db2254d3984b5e719dfc0d433048e74176bfa068`; after the original packet base,
only unrelated sidecar support/acceptance artifacts advanced dev before
closeout.

This followup is a freshness pass and reviewer decision packet for the same
AG-BE-ID-003 blocker:

1. restate the current active parent blocker from task state
2. confirm AG-BE-ID-002 remains archived done and usable as the servant ensure
   composition source
3. re-check the servant-session OpenAPI, package router, BFF implementation,
   capability manifest, and legacy session routes at current dev tip
4. give the parent owner/reviewer an explicit decision request before any
   implementation work
5. update the frontend and operator gates that downstream work should honor

This packet does not approve, reopen, or implement the parent task.

## 2. Sources Checked

| Source | Why it matters |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Parent remains active and blocked waiting for `Claude` to decide the servant-session type contract. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Upstream servant ensure/provision/reconcile is archived done and merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Confirms followup 4 is archived done, not review-pending. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` | Immediate predecessor packet and decision matrix. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | Defines `/bff/agora/servant/sessions` route family and incomplete create schema. |
| `services/control-plane/specs/agora/capability_manifest.json` | Still lists legacy session prefixes, not `/bff/agora/servant/sessions`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md` | Common OpenClaw skill envelope includes `session_type`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-dialogue/SPEC.md` | Strategy dialogue allows `interactive` and `trainer`, not `research_task`. |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent acceptance still requires interactive/trainer/research sessions, SSE, audit fields, and `OPENCLAW_UPSTREAM_DEGRADED`. |
| `services/control-plane/bff/agora/router.py` and `services/control-plane/bff/agora/servant/router.py` | Confirms the package router registers servant ensure only; no servant-session routes are included. |
| `services/control-plane/bff/main.py` and `services/control-plane/bff/read_store.py` | Legacy sessions still default/filter on `quick_ask`; legacy SSE alias is not session-scoped. |
| `services/control-plane/bff/models.py` | Current BFF error enum has `DEPENDENCY_UNAVAILABLE`, not `OPENCLAW_UPSTREAM_DEGRADED`. |

## 3. Current Parent State

`AG-BE-ID-002` is complete. Its archived delivery records merge target
`247211c2208d15bce628c017044a3bf2062603e6`, and its local closeout verification
covered the Agora router, OpenClaw persona-agent sync, and identity-scope tests.
AG-BE-ID-003 can therefore treat `POST /bff/agora/servant/ensure` as the
upstream profile/provisioning surface.

`AG-BE-ID-003` remains blocked before implementation. Its active status says
the task requires `POST /bff/agora/servant/sessions` for
`interactive`, `trainer`, and `research_task`, but the canonical create request
contract does not identify where a compliant client supplies the session type or
how the BFF derives it.

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

The schema has no `session_type` or `sessionType` field. Because
`additionalProperties` is `false`, a strict frontend cannot send either field in
the top-level request.

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

### Decision needed from parent reviewer

The parent owner/reviewer should choose one of these paths explicitly:

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract-compliant clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred, lowest ambiguity. |
| Add an explicit equivalent such as `session_kind` | Same as above if the field is documented, required or deliberately optional, and mapped to OpenClaw. | Acceptable if named by reviewer. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Risky unless the derivation rule is deterministic and documented. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract; not strict-mode ready unless explicitly approved as public contract. |
| Default all creates to one type | Quick to implement. | Should remain blocked; it conflicts with parent acceptance for three visible types. |

## 5. Current Route Evidence

| Surface | Current observation at dev `7e993734` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, `GET`, `messages`, `terminate`, and `stream`. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks session type. | Blocks strict create UI and parent implementation. |
| Agora package router | Includes identity, servant, strategy workshop, research, trading room, dashboard, shadow, personalization, and management projection routers. | No servant session router is registered. |
| Servant router | Implements servant ensure/provision/reconcile behavior. | AG-BE-ID-003 must not overwrite AG-BE-ID-002 ensure behavior. |
| BFF runtime | Focused search found no BFF implementation for `servant/sessions`. | Parent still needs implementation after decision. |
| Capability manifest | `agora.session.v1` lists `/bff/agora/ask/sessions` and `/bff/agora/sessions`, not `/bff/agora/servant/sessions`. | Frontend discovery is stale relative to OpenAPI v1.1. |
| Legacy `/bff/agora/sessions` | `main.py` and `read_store.py` still default mode to `quick_ask`. | Not a safe substitute for servant sessions. |
| Legacy `/bff/agora/ask/sessions` | Hardcodes and filters `mode == "quick_ask"`. | Covers ask sessions only. |
| Legacy SSE | `GET /bff/sse/agora/sessions/{sessionId}` delegates to `stream_ask_events()` and ignores `sessionId` for filtering. | Not proof of session-scoped servant SSE. |
| Degraded error | BFF enum and handlers use `DEPENDENCY_UNAVAILABLE`; no `OPENCLAW_UPSTREAM_DEGRADED` match was found. | Parent must preserve the accepted degradation code or get reviewer approval for an envelope mapping. |

## 6. Frontend Handoff

Until the parent records the type-contract decision and lands the route family,
execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged. |
| Display capability hints | `GET /bff/agora/capabilities` | Manifest does not yet expose `/bff/agora/servant/sessions`. |

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

### Recommended client shape after parent decision

If the parent approves an explicit public type field, execute-plans can expose an
ergonomic client similar to:

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

## 7. Operator Journey

### Before parent decision

1. Operator resolves Agora identity through `GET /bff/agora/me`.
2. Operator ensures the private servant through `POST /bff/agora/servant/ensure`.
3. UI shows servant readiness and capability hints.
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

## 8. Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| P0 upstream servant | Compose with merged `POST /bff/agora/servant/ensure` from AG-BE-ID-002. |
| P1 type contract | Record how the create request carries or derives `interactive`, `trainer`, and `research_task`. |
| P2 OpenAPI/schema alignment | Update or explicitly approve the public create contract; do not accept undeclared top-level fields. |
| P3 research mapping | Name the OpenClaw skill/session kind that owns `research_task`. |
| P4 package placement | Add servant-session logic without overwriting AG-BE-ID-002 ensure behavior. Prefer a servant/session package boundary over more `main.py` logic. |
| P5 capability discovery | Align `capability_manifest.json` or record why frontend discovery should use another source for servant sessions. |
| P6 audit fields | Include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` for reads/writes and response meta. |
| P7 degradation code | Preserve `OPENCLAW_UPSTREAM_DEGRADED` as the accepted session degradation code or get reviewer approval for a precise mapping. |
| P8 SSE scope | Implement servant session stream scoped by `session_id`; do not reuse the shared ask-channel stream as proof. |
| P9 legacy route policy | State whether legacy `/bff/agora/sessions` remains compatibility-only, becomes an alias, or is out of scope. |
| P10 tests | Cover create for all approved types, invalid/missing type handling, message post, terminate, stream, audit meta, idempotency, capability discovery, and degradation. |

## 9. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
./scripts/git/task_start.sh "AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5"
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
rg -n "ServantSessionCreateRequest|/bff/agora/servant/sessions|createServantSession|session_type|sessionType|interactive|trainer|research_task|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/openapi/agora_v1_1.openapi.yaml
rg -n "session_type|sessionType|interactive|trainer|research_task|servant/sessions|ServantSessionCreateRequest|OPENCLAW_UPSTREAM_DEGRADED" docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md scripts/dispatch_agora_cross_repo_2026-06-20.py
rg -n "servant/sessions|createServantSession|OPENCLAW_UPSTREAM_DEGRADED|DEPENDENCY_UNAVAILABLE" services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora
rg -n "@.*bff/agora/sessions|@.*bff/agora/ask/sessions|@.*bff/sse/agora/sessions|create_agora_session|quick_ask|terminate" services/control-plane/bff/main.py services/control-plane/bff/read_store.py
rg -n "agora.session.v1|/bff/agora/sessions|/bff/agora/ask/sessions|/bff/agora/servant/sessions" services/control-plane/specs/agora/capability_manifest.json
```

Results:

- Task branch was reset to current `origin/dev` before edits.
- Followup 5 is active, owner `Codex`, reviewer `Codex2`.
- Followup 4 is archived done through closeout PR #1897 at merge
  `e51bc8fdcdce119bd66596367c468364d18bf835`.
- Parent `AG-BE-ID-003` remains blocked waiting for `Claude` on the
  servant-session `session_type` contract decision.
- Upstream `AG-BE-ID-002` is done and merged.
- OpenAPI v1.1 defines servant session routes but no type field in
  `ServantSessionCreateRequest`.
- BFF has no `servant/sessions` implementation today.
- BFF/OpenAPI/spec search did not find `OPENCLAW_UPSTREAM_DEGRADED`.
- Legacy session routes still live in `main.py`; legacy create defaults to
  `quick_ask`, and the legacy session SSE route aliases the shared ask stream.

## 10. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document was edited.
- No OpenAPI, capability manifest, BFF runtime, router, registry, governance,
  migration, schema, or frontend implementation was changed.
- The intended support packet artifact is this file:
  `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`.

## 11. Reviewer Handoff

Reviewer: `Codex2`

Review outcome: approved in `ai-status.json`.

Reviewer notes:

- Support-only scope is compliant: the packet adds support material and does
  not modify L1 canonical truth, OpenAPI, BFF runtime, capability manifest,
  registry, governance, database migration, or frontend source.
- Freshness was sanity checked against `origin/dev`
  `81b17d678b4c029522a32eb26d9eb218a2350279` at review time.
- Parent blocker restatement is accurate: `ServantSessionCreateRequest` still
  lacks a public session type/kind contract, and the `research_task` mapping
  still needs the parent reviewer decision.
- Frontend/operator gates and parent absorption gates are actionable.

## 12. Closeout Finalization Addendum

Closeout owner: `Codex`

Additional closeout freshness check:

```bash
git diff --name-only 81b17d678b4c029522a32eb26d9eb218a2350279..origin/dev
git diff --stat 81b17d678b4c029522a32eb26d9eb218a2350279..origin/dev -- services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora docs/04/pantheon_agora_cross_repo_2026-06-20 scripts/dispatch_agora_cross_repo_2026-06-20.py support/sidecars/AG-BE-ID-003 .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_5.md
gh pr view 1901 --json number,state,mergedAt,mergeCommit,url,baseRefName,headRefName
```

Results:

- PR #1901 is merged into `dev` at merge commit
  `4a6a593d0edd33e6ac4d3b17e533ff047dd38530`.
- `origin/dev` advanced from the reviewer freshness base to
  `db2254d3984b5e719dfc0d433048e74176bfa068`.
- The only files changed in that range are unrelated sidecar task briefs and
  support packets for `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`,
  `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-4`, and
  `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14`.
- No AG-BE-ID-003 support packet, servant-session OpenAPI, BFF runtime,
  capability manifest, Agora design-closure, or dispatch script surface changed
  after review.

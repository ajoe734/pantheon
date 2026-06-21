# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 12

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-21` |
| Status | `review_approved; owner closeout recorded before formal done` |
| Current dev base | `1cedc9791180fee9e38dbf1fa856383fd0afcf81` |
| Previous sidecar closeout merge | `bfb6b1c640db2a19a3ce025aa8d29982b9164a0b` |
| Previous reviewed packet merge | `9880c81584ab3b6985c197916674ad073680dd3d` |
| Reviewed packet PR | `#1964` merged at `321414475757e663317c194522adc76c37f7b3d7` |
| Closeout PR refresh base | `origin/dev` at `0841c0f9e2dfe39aa8c1486bc4f9b8c87a22d684` after PR `#1980` reported `BEHIND` |
| New relevant dev merge | `e5f20720` / PR `#1952` for `AG-XR-002A` |
| New relevant sidecar merge | `285a6d60` / PR `#1954` for `AG-XR-002A-SIDECAR-BFF-HANDOFF` |
| New compatibility support merge | `e7d75a11` / PR `#1956` for `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` |
| New review artifact merge | `f8a8dd73` / PR `#1957` for `AG-XR-002A` |
| New sidecar closeout merge | `4588fe17` / PR `#1959` for `AG-XR-002A-SIDECAR-BFF-HANDOFF` |
| New unrelated design-closure archive | `52a2d5a8` / PR `#1961` for `AG-BE-SW-001` |
| New unrelated BFF runtime merge | `270340d3` / PR `#1960` for management `nl/ask` async provider finalization |
| New downstream frontend support merge | `a2d16e4c` / PR `#1955` for `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` |
| New unrelated OpenClaw e2e merge | `1cedc979` / PR `#1962` for persona OpenClaw adapter route-backed flow test |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, database migrations, OpenClaw adapter code,
compatibility manifest source, or execute-plans source files.

## 1. Purpose

Followup 11 is archived `done`. Its reviewed packet PR #1948 merged at
`9880c81584ab3b6985c197916674ad073680dd3d`, and its owner closeout PR #1950
merged at `bfb6b1c640db2a19a3ce025aa8d29982b9164a0b`.

Current `origin/dev` for this followup is
`1cedc9791180fee9e38dbf1fa856383fd0afcf81`. Since followup-11 closeout, `dev`
advanced through:

- AG-FE-DB-002 support-only followup-12 packet and review material.
- AG-XR-002A parent work refreshing execute-plans Agora v1.1 generated types,
  contract drift scripts, and the dev compatibility manifest frontend half.
- AG-XR-002A support-only BFF/frontend handoff packet.
- AG-XR-003 support-only followup-14 acceptance packet, which records local
  manifest sanity as improved while execute-plans PR #63 and runtime pin remain
  blockers.
- AG-XR-002A review-artifact materialization and closeout, which records
  Claude's approval, PR #1952 merge evidence, and archived `done` status.
- AG-XR-002A sidecar closeout, which archives the support-only handoff as
  `done`.
- AG-BE-SW-001 deep design-closure archive material. A targeted grep for
  AG-BE-ID-003/servant-session/type-contract keywords returned no matches, so
  it does not alter this packet's parent blocker conclusion.
- Management `POST /bff/management/nl/ask` async provider finalization. A
  targeted diff check showed only management NL provider/session bookkeeping,
  not Agora servant-session implementation.
- AG-FE-ID-001 followup-22 downstream support packet, which rechecks the
  frontend shell/client handoff and keeps `AG-BE-ID-003` as the session gate.
- Persona OpenClaw adapter route-backed e2e test refresh. Targeted grep found
  no AG-BE-ID-003 servant-session/type-contract keywords in that test delta.

This is a meaningful compatibility-context delta, but it is not an
AG-BE-ID-003 runtime or servant-session contract implementation. The parent
`AG-BE-ID-003` remains blocked on Claude's decision for how the public create
contract carries or derives `interactive`, `trainer`, and `research_task`.

This packet does not approve, reopen, or implement parent `AG-BE-ID-003`.

Owner closeout note: Codex2 approved this support-only packet after PR #1964
merged into `dev` at `321414475757e663317c194522adc76c37f7b3d7`. This closeout
records the accepted review state and the support-only boundary before the task
is formally archived with `AI_NAME=Codex ./scripts/ai-status.sh done`. No L1
truth, OpenAPI, BFF runtime, route registry, governance, database, OpenClaw
adapter, compatibility manifest source, or execute-plans source path is changed
by this closeout. PR #1980 initially reported `BEHIND`; the branch was refreshed
by merging `origin/dev` at `0841c0f9e2dfe39aa8c1486bc4f9b8c87a22d684`, then
this task-owned closeout refresh note was recorded so the final branch HEAD
continues to carry the task id and required trailers.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex` and read the central status root via
`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`.

| Task | Status | Handoff implication |
|---|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | `in_progress`; owner `Codex`, reviewer `Codex2` | This packet is the support-only artifact for review. |
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | archived `done`; reviewed packet PR #1948 and closeout PR #1950 merged | Previous AG-BE-ID-003 handoff is durable and still says the parent is blocked. |
| `AG-BE-ID-003` | `blocked`; owner `Codex2`, reviewer `Claude`, waiting for `Claude` | Parent implementation must not proceed until the servant session type contract is decided. |
| `AG-BE-ID-002` | archived `done` | `/bff/agora/servant/ensure` is the accepted upstream servant ensure/provision/reconcile surface. |
| `AG-XR-002A` | archived `done`; PR #1952 and closeout PR #1957 merged | v1.1 frontend type generation and manifest frontend hash parity are durable support context. |
| `AG-XR-002A-SIDECAR-BFF-HANDOFF` | archived `done`; original packet PR #1954 and closeout PR #1959 merged | Support-only BFF/frontend handoff exists for the AG-XR-002A delta; it is not AG-BE-ID-003 runtime readiness. |
| `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | `review_approved`; reviewer reassigned to `Claude`; PR #1956 merged | Latest compatibility support packet says local v1.1 sanity improved, but execute-plans PR #63/runtime pin/deployment gate still block parent done. |
| `AG-XR-003` | `in_progress`; owner `Codex2`, reviewer `Claude2`; depends on `AG-XR-002A` | Compatibility work is moving again, but still needs cross-repo PR/runtime-pin/deployment-gate disposition. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | archived `done`; PR #1949 merged | Latest frontend support packet is durable but predates AG-XR-002A and does not unblock session UI. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` | `review`; PR #1955 merged | Latest downstream frontend support packet says target shell/client files remain absent and `AG-BE-ID-003` remains the session gate. |
| `AG-FE-ID-001` | `todo`; depends on `AG-FE-000` and `AG-BE-ID-003` | Frontend parent implementation has not started in durable task state. |

Dependency honesty rule: the frontend may use identity, servant ensure, and
AG-XR-002A type/manifest refresh as support context, but it must not claim
interactive, trainer, or research-task session readiness while
`AG-BE-ID-003` is blocked.

## 3. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_12.md` | This task-scoped assignment and support-only boundary. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | Confirms active sidecar state, owner, reviewer, artifact, and support-only acceptance. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003` | Confirms parent remains blocked on the servant session type-contract decision. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Confirms predecessor archived `done`, with PR #1948 and closeout PR #1950 merged. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-002A` | Confirms v1.1 generated types / manifest frontend half are archived `done` after PR #1952 and closeout PR #1957. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-002A-SIDECAR-BFF-HANDOFF` | Confirms the AG-XR-002A sidecar packet is archived `done` after PR #1954 and closeout PR #1959. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14` | Confirms the latest AG-XR-003 support packet is `review_approved` after PR #1956, with Claude review approval recorded. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003` | Confirms compatibility task is now `in_progress` and depends on AG-XR-002A, rather than the prior blocked state. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` | Confirms latest frontend support packet is archived `done`. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` | Confirms the newest frontend support packet is in `review` after PR #1955 and keeps AG-BE-ID-003 as the session gate. |
| `AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001` | Confirms frontend parent remains `todo` and still depends on `AG-BE-ID-003`. |
| `git merge --ff-only` / `git merge --no-edit origin/dev` | Refreshed this task branch from followup-11 closeout merge `bfb6b1c6` through `270340d3`, then merged `a2d16e4c` and `1cedc979` as downstream frontend and unrelated e2e material landed. |
| `git log --oneline bfb6b1c6..origin/dev` | Shows AG-FE-DB-002 followup-12, AG-XR-002A parent/sidecar/review/closeout PRs, AG-XR-003 followup-14, AG-BE-SW-001 design-closure archive, management nl/ask async closeout, AG-FE-ID-001 followup-22, and persona OpenClaw e2e test refresh after followup 11. |
| `git diff --name-status bfb6b1c6..origin/dev -- ...` | Shows no Agora servant-session runtime, OpenAPI, or AG-BE-ID-003 support-path implementation delta; compatibility/typegen/support files changed, plus an unrelated management `nl/ask` BFF runtime update. |
| `git diff -U0 52a2d5a8..HEAD -- services/control-plane/bff/main.py \| rg ...` | Confirms the latest BFF runtime diff is management NL provider finalization around `POST /bff/management/nl/ask`, not Agora servant/session logic. |
| `docs/contracts/agora/dev-compatibility-manifest.json` | Frontend generated contract/hash placeholders were filled, but `compatibility_status` remains `pending` due to `frontend-runtime-commit-placeholder`. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated types now explicitly carry `WidgetSpecV1` while retaining `WidgetSpec` as an alias and `WidgetSpecV2` separately. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | `ServantSessionCreateRequest` still lacks a public session type field while the servant session route family exists on paper. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime servant router still owns `/servant/ensure`; no servant-session runtime routes are implemented there. |
| `services/control-plane/bff/main.py` | Legacy `/bff/agora/sessions*` routes and SSE alias remain in `main.py`; stream alias still ignores `sessionId`. |
| `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md` | New support packet states generated clients may expose routes that strict live BFF cannot satisfy yet. |
| `support/sidecars/AG-XR-003/AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14.md` | Latest AG-XR support packet says deployment gate still fails closed while frontend runtime commit is a placeholder. |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22.md` | New downstream frontend support packet confirms AG-FE-ID-001 target files are still absent and AG-BE-ID-003 remains the session dependency. |
| `rg -n "...keywords..." tests/e2e/test_persona_openclaw_adapter_backed_flow_100.py` | Targeted grep found no AG-BE-ID-003, servant session, session type, research_task, degradation, or management NL keywords in the new e2e test delta. |
| `docs/reviews/2026-06-21-ag-xr-002a-claude-review.md` | Materialized Claude approval for AG-XR-002A; records PR #1952 merge evidence. |
| `rg -n "...keywords..." docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md` | Targeted grep found no AG-BE-ID-003, servant session, session type, or degradation keywords in the new SW001 closure archive. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 4. Delta Since Followup 11

| Change | What changed | Parent implication |
|---|---|---|
| Followup 11 closed | Archived `done`; reviewed packet PR #1948 and closeout PR #1950 are merged. | Treat followup 11 as accepted support evidence. |
| Dev advanced to `1cedc979` | AG-FE-DB-002 followup-12 support/closeout material, AG-XR-002A parent/sidecar/review/closeout material, AG-XR-003 followup-14, AG-BE-SW-001 design-closure archive, management `nl/ask` async BFF closeout, AG-FE-ID-001 followup-22, and persona OpenClaw e2e test refresh landed. | Additional support, compatibility, downstream frontend, unrelated BFF, and unrelated e2e context landed, but no AG-BE-ID-003 Agora servant-session implementation changed. |
| AG-XR-002A parent landed | `execute-plans/src/lib/bff-v1/agora/types.ts`, typegen scripts, drift check, manifest test, and frontend manifest half were refreshed. | The old "frontend generated types still v1" blocker is materially improved. |
| AG-XR-002A closed | `docs/reviews/2026-06-21-ag-xr-002a-claude-review.md` and task brief were added via PR #1957; central status now archives AG-XR-002A as `done`. | Review and owner closeout evidence are durable, but this still does not implement AG-BE-ID-003. |
| AG-XR-002A sidecar closed | AG-XR-002A sidecar closeout PR #1959 merged; central status archives the sidecar as `done`. | Support packet is durable context, not AG-BE-ID-003 runtime readiness. |
| AG-XR-003 followup-14 landed | Support packet records local manifest sanity, contract drift, and build improvements after AG-XR-002A. | It also says execute-plans PR #63 remains open/unstable and deployment gate still fails closed. |
| Manifest blocker narrowed | `frontend-generated-contract-commit-placeholder` and `frontend-generated-types-not-agora-v1.1` were removed. | Cross-repo compatibility is closer, but still not deploy-compatible. |
| Manifest still pending | `compatibility_status` remains `pending`; blocking reason is `frontend-runtime-commit-placeholder`. | Strict live frontend and AG-XR-003 completion still need runtime commit/deployment disposition. |
| AG-XR-003 status changed | Central status now shows `in_progress`, owner `Codex2`, reviewer `Claude2`, depends on `AG-XR-002A`; previous followup-11 packet described it as blocked. | Followup-12 supersedes that narrow status fact, but not the AG-BE-ID-003 blocker. |
| AG-BE-SW-001 archive landed | New deep closure archive did not match targeted AG-BE-ID-003/servant-session/type-contract keywords. | No change to this sidecar's parent blocker conclusion. |
| Management NL ask async landed | `services/control-plane/bff/main.py` changed, but targeted diff hits are management NL provider finalization and session bookkeeping only. | BFF changed, but not the Agora servant-session route family or type contract blocking AG-BE-ID-003. |
| AG-FE-ID-001 followup-22 landed | Downstream frontend support packet confirms parent `AG-FE-ID-001` remains `todo`, target shell/client files remain absent from checked execute-plans remotes, and `AG-BE-ID-003` remains blocked. | Reinforces the session/front-end gate; does not unblock AG-BE-ID-003. |
| Persona OpenClaw e2e refresh landed | New e2e test delta had no targeted servant-session/type-contract keyword hits. | No change to this sidecar's parent blocker conclusion. |
| Checked AG-BE pathset | No diff from `bfb6b1c6..origin/dev` over OpenAPI, Agora specs, AG-BE-ID-003 support path, or servant-session implementation paths; BFF diff is unrelated management NL ask async work. | No new evidence unblocks AG-BE-ID-003. |
| Frontend support state | AG-FE-ID-001 followup-21 is archived `done`; parent `AG-FE-ID-001` remains `todo`. | Frontend remains downstream and must not enable servant-session UI before AG-BE-ID-003 lands. |

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

### D2 - Generated v1.1 types do not solve the BFF decision

AG-XR-002A improved the frontend generated type mirror, but it generated from
the same OpenAPI create schema. It did not add or approve a field that carries
`interactive`, `trainer`, or `research_task` into AG-BE-ID-003.

### D3 - OpenClaw session invocation still needs a type

The BFF-to-OpenClaw session creation path needs a deterministic session type.
AG-BE-ID-003 cannot safely invent that mapping during implementation.

### D4 - Research-task mapping remains unresolved

Checked evidence still names interactive/trainer-like surfaces, but no
reviewer-approved OpenClaw skill/session kind for `research_task` has been
recorded in the parent task state.

### D5 - Compatibility progress is not backend readiness

AG-XR-002A narrows the cross-repo compatibility gap, but it did not add
servant-session BFF runtime behavior, OpenAPI session type fields, route
handlers, or frontend session clients. It should be treated as compatibility
support context, not AG-BE-ID-003 readiness.

## 6. Decision Options For Parent Reviewer

| Option | Effect | Sidecar view |
|---|---|---|
| Add an explicit public `session_type` field to `ServantSessionCreateRequest` | Contract clients can send `interactive`, `trainer`, or `research_task`; OpenAPI can validate it. | Preferred because it is least ambiguous. |
| Add an explicit equivalent such as `session_kind` | Same result if the field is documented and mapped to OpenClaw. | Acceptable if reviewer names the field. |
| Derive type server-side from route/action/context | Public schema stays unchanged. | Acceptable only with a deterministic, documented derivation rule. |
| Use `metadata.session_type` | Currently schema-allowed because metadata is open. | Hidden contract unless explicitly promoted by reviewer. |
| Default all creates to one type | Quick to code. | Should stay blocked; it fails parent acceptance for three visible types. |

## 7. Current Route Evidence

| Surface | Current observation at dev `1cedc979` | Readiness impact |
|---|---|---|
| OpenAPI v1.1 | Defines `POST /bff/agora/servant/sessions`, get, messages, terminate, and stream. | Route family exists on paper. |
| OpenAPI create body | References `ServantSessionCreateRequest`, which lacks a session type field. | Blocks strict create UI and parent implementation review. |
| v1.1 generated types | `execute-plans` mirror includes servant session routes and explicit v1/v2 widget names. | Useful generated contract surface, not runtime proof. |
| Dev compatibility manifest | Frontend generated contract/hash fields are filled; status remains `pending` because frontend runtime commit is still a placeholder. | Strict live frontend remains gated pending AG-XR-003 and execute-plans PR #63 disposition. |
| Servant router | Implements servant ensure/provision/reconcile behavior only. | AG-BE-ID-003 must compose with this route and not overwrite AG-BE-ID-002 behavior. |
| BFF runtime | No BFF implementation for `/bff/agora/servant/sessions` was found in checked runtime paths. | Parent still needs implementation after the type decision. |
| Legacy `/bff/agora/sessions` | Existing `main.py` route creates legacy ask/session records, accepting `mode` or `sessionType` and defaulting to `quick_ask`. | Not a safe substitute for servant sessions. |
| Legacy SSE alias | `GET /bff/sse/agora/sessions/{sessionId}` delegates to `stream_ask_events()` and does not use `sessionId`. | Not proof of servant session-scoped SSE. |
| Degraded error | `OPENCLAW_UPSTREAM_DEGRADED` was not found in checked BFF runtime paths. | Parent must preserve accepted degradation semantics or get reviewer approval for a precise mapping. |
| Frontend parent state | `AG-FE-ID-001` remains `todo` and depends on `AG-BE-ID-003`. | No frontend strict-live enablement yet. |

## 8. Frontend Handoff

Until the parent records the type-contract decision and lands the runtime route
family, execute-plans should keep servant-session create/message/stream/terminate
controls disabled in strict live mode.

### Safe now

| Frontend action | Surface | Caveat |
|---|---|---|
| Resolve operator Agora scope | `GET /bff/agora/me` | Identity scope only. |
| Display capability readiness | `GET /bff/agora/capabilities` or manifest context | Live route may still serve the frozen v1 manifest; v1.1 extension readback is not proven by AG-XR-002A alone. |
| Show servant readiness after user action | `POST /bff/agora/servant/ensure` | Upstream AG-BE-ID-002 is merged. |
| Use generated v1.1 types | `execute-plans/src/lib/bff-v1/agora/types.ts` | Generated contract surface only; do not treat unimplemented routes as live. |
| Use AG-XR-002A support packet as planning input | `support/sidecars/AG-XR-002A/AG-XR-002A-SIDECAR-BFF-HANDOFF.md` | Handoff context only; not AG-BE-ID-003 runtime proof. |

### Still blocked

| Frontend action | Blocker |
|---|---|
| Create interactive servant session | No public create contract field or derivation rule for `interactive`. |
| Create trainer servant session | Same type blocker, though trainer appears in existing strategy/training surfaces. |
| Create research-task servant session | No named `research_task` skill/session mapping in checked evidence. |
| Send servant session message | OpenAPI path and generated types exist, but BFF implementation is absent. |
| Terminate servant session | OpenAPI path and generated types exist, but BFF implementation is absent. |
| Stream servant session events | OpenAPI path and generated types exist, but BFF implementation is absent and legacy SSE is not session-scoped. |
| Show accepted OpenClaw degraded state | `OPENCLAW_UPSTREAM_DEGRADED` was not found in checked BFF runtime paths. |
| Claim strict v1.1 cross-repo compatibility | Manifest is still `pending` with `frontend-runtime-commit-placeholder`; AG-XR-003 followup-14 says execute-plans PR #63 remains open/unstable. |
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
| P5 discovery source | Decide whether live `/bff/agora/capabilities` must expose the v1.1 extension manifest or whether generated manifest parity is sufficient for this phase. |
| P6 cross-repo compatibility | Keep strict live FE gated until `AG-XR-003` records compatible status or reviewer/ops records an explicit disposition for remaining pending fields. |
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
3. delta assessment after followup 11 is accurate
4. AG-XR-002A is treated as type/manifest progress, not AG-BE-ID-003 runtime readiness
5. AG-XR-003 followup-14 is treated as compatibility support context, not deployment compatibility approval
6. AG-FE-ID-001 session UI and AG-XR-003 compatibility gates remain conservative
7. parent absorption gates remain actionable and do not implement canonical truth

## 12. Verification Run

Commands run while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-11
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-002A
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-002A-SIDECAR-BFF-HANDOFF
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-14
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-XR-003
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-21
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-22
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-FE-ID-001
git merge --ff-only origin/dev
git merge --no-edit origin/dev
git merge --no-edit origin/dev
git merge --ff-only origin/dev
git merge --ff-only origin/dev
git merge --ff-only origin/dev
git merge --ff-only origin/dev
git merge --ff-only origin/dev
git log --oneline bfb6b1c640db2a19a3ce025aa8d29982b9164a0b..origin/dev
git diff --name-status bfb6b1c640db2a19a3ce025aa8d29982b9164a0b..origin/dev -- services/control-plane/bff services/control-plane/openapi services/control-plane/specs/agora docs/contracts/agora scripts/agora_compat_manifest.py scripts/test_agora_compat_manifest.py execute-plans/src/lib/bff-v1/agora execute-plans/scripts support/sidecars/AG-BE-ID-003 support/sidecars/AG-FE-ID-001 support/sidecars/AG-XR-003 support/sidecars/AG-XR-002A docs/reviews/2026-06-21-ag-xr-002a-claude-review.md .orchestrator/task-briefs/ag_xr_002a.md docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure
git diff bfb6b1c640db2a19a3ce025aa8d29982b9164a0b..origin/dev -- docs/contracts/agora/dev-compatibility-manifest.json
git diff bfb6b1c640db2a19a3ce025aa8d29982b9164a0b..origin/dev -- execute-plans/src/lib/bff-v1/agora/types.ts
rg -n "ServantSessionCreateRequest|session_type|sessionType|session_kind|/bff/agora/servant/sessions|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/bff services/control-plane/specs/agora docs/contracts/agora/dev-compatibility-manifest.json execute-plans/src/lib/bff-v1/agora/types.ts
rg -n "AG-BE-ID-003|ServantSessionCreateRequest|servant/sessions|research_task|session_type|sessionType|interactive|trainer|OPENCLAW_UPSTREAM_DEGRADED" docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md
git diff -U0 52a2d5a8cf6eff9e6fda7d98d170d389196cc29c..HEAD -- services/control-plane/bff/main.py | rg -n "agora|servant|session|OPENCLAW_UPSTREAM_DEGRADED|management/nl|nl/ask|ask|provider"
rg -n "AG-BE-ID-003|servant|ServantSession|session_type|sessionType|research_task|interactive|trainer|OPENCLAW_UPSTREAM_DEGRADED|management/nl" tests/e2e/test_persona_openclaw_adapter_backed_flow_100.py
```

Final validation run before commit:

```bash
env GIT_INDEX_FILE=/tmp/git-index-check-AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12 git read-tree HEAD
env GIT_INDEX_FILE=/tmp/git-index-check-AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12 git add .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_12.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md
env GIT_INDEX_FILE=/tmp/git-index-check-AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12 git diff --cached --check -- .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_12.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md
rg -n "^(TBD|TODO|PLACEHOLDER|FIXME)$" .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_12.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md
python3 scripts/agora_schema_bundle.py --verify
python3 -m pytest scripts/test_agora_compat_manifest.py -q
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q
```

Result:

- private-index staged `git diff --cached --check` passed.
- placeholder scan returned no matches.
- `scripts/agora_schema_bundle.py --verify` passed.
- `scripts/test_agora_compat_manifest.py`: 4 passed.
- `services/control-plane/bff/tests/test_agora_router.py`: 18 passed.

Owner closeout verification after review approval:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12
gh pr view 1964 --json number,state,mergedAt,mergeCommit,url,headRefName,baseRefName,title
git branch -r --contains HEAD
git rev-parse origin/dev
git merge --no-edit origin/dev
git diff --check -- .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_12.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md
rg -n "^(TBD|TODO|PLACEHOLDER|FIXME)$" .orchestrator/task-briefs/ag_be_id_003_sidecar_bff_handoff_followup_12.md support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md
```

Result:

- PR #1964 is merged with merge commit
  `321414475757e663317c194522adc76c37f7b3d7`.
- Current branch is the expected task branch.
- `HEAD` is already contained in `origin/dev`; a separate closeout commit will
  carry only this accepted-review record.
- PR #1980 initially reported `BEHIND`; the branch was refreshed by merging
  `origin/dev` at `0841c0f9e2dfe39aa8c1486bc4f9b8c87a22d684`.
- `git diff --check` passed for the closeout artifact paths.
- placeholder scan returned no matches.

## 13. Handoff Recommendation

Recommended status transition after commit and PR:

```bash
AI_NAME=Codex PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon ./scripts/ai-status.sh handoff AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12 Codex2 "Followup-12 support-only BFF/frontend handoff packet is ready for review at support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md. Scope changed only the task brief and support packet; no canonical truth, OpenAPI, Agora servant-session runtime, route registry, governance, compatibility manifest source, OpenClaw adapter, or execute-plans source paths changed. Delta since followup-11: AG-XR-002A and its BFF handoff sidecar are archived done; AG-XR-003 is in_progress and followup-14 is review_approved but still says execute-plans PR #63/runtime pin/deployment gate block compatibility done; AG-FE-ID-001 followup-22 is in review and confirms frontend shell/client files remain absent; latest AG-BE-SW-001 closure archive and persona OpenClaw e2e refresh had no targeted servant-session/type-contract keyword hits; BFF main.py advanced for unrelated management nl/ask async provider finalization; AG-BE-ID-003 remains blocked on the servant session type contract decision."
```

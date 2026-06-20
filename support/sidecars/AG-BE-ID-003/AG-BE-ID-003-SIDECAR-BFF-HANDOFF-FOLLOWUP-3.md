# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet - Followup 3

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper parent | `AG-BE-ID-003` - Interactive/trainer/research session BFF facade |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `ready_for_review` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance policy, persona or registry state, database migrations,
or execute-plans source files.

## 1. Purpose

This third followup packet updates the prior AG-BE-ID-003 sidecar findings for
the current parent state. Followup-2 remains accurate for the legacy
`/bff/agora/sessions` surface, but the parent task has since been blocked on a
more specific contract issue: the canonical Agora v1.1 route family for the
facade is `/bff/agora/servant/sessions`, while
`ServantSessionCreateRequest` does not define a `session_type` field and has
`additionalProperties: false`.

This packet gives the parent owner and downstream frontend team a narrow
decision map:

1. confirm AG-BE-ID-002 is ready to compose with
2. identify the AG-BE-ID-003 blocker before implementation
3. separate the canonical `servant/sessions` route family from legacy
   `/bff/agora/sessions` and `/bff/agora/ask/sessions`
4. restate the frontend gates for `interactive`, `trainer`, and
   `research_task` session UI

## 2. Sources Checked

| Source | Why it matters |
|---|---|
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003` | Parent is currently blocked on the `session_type` contract mismatch. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002` | Upstream servant ensure task is archived done; merge target SHA `247211c2208d15bce628c017044a3bf2062603e6`. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF.md` | Original gap assessment for legacy session surface. |
| `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Followup with `ask/sessions` split, `quick_ask` default, and identity-router scope findings. |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent acceptance requires `interactive`, `trainer`, and `research` sessions, SSE, audit fields, and `OPENCLAW_UPSTREAM_DEGRADED`. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | Defines `/bff/agora/servant/sessions`, messages, terminate, and stream routes; create request lacks `session_type`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md` | Common OpenClaw skill envelope includes `session_type`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-dialogue/SPEC.md` | Strategy dialogue skill allows `interactive` and `trainer` sessions only. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Frozen route catalog still lists legacy `/bff/agora/sessions` and `/bff/agora/ask/sessions`. |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen manifest lists legacy session prefixes but not `/bff/agora/servant/sessions`. |
| `services/control-plane/bff/agora/servant/router.py` | AG-BE-ID-002 ensure route exists here; no servant session route is registered. |
| `services/control-plane/bff/main.py` and `services/control-plane/bff/read_store.py` | Legacy `/bff/agora/sessions` still defaults to `quick_ask`; SSE alias still streams the shared ask channel. |

## 3. Current Parent State

`AG-BE-ID-002` is no longer the blocker. It is archived `done`, with the BFF
servant ensure path merged through the dev target. The AG-BE-ID-003 parent can
compose with `POST /bff/agora/servant/ensure` and the resulting servant profile
metadata.

`AG-BE-ID-003` itself is blocked before implementation. The parent status says
the task requires a servant session facade for `interactive`, `trainer`, and
`research_task`, but the canonical v1.1 OpenAPI request contract does not say
where the client supplies that session type or how the BFF derives it.

This is a contract blocker, not an implementation gap that the owner should
fill by guesswork.

## 4. New Finding: Servant Session Contract Is Incomplete

Agora v1.1 OpenAPI defines these servant session routes:

| Method | Path | OpenAPI operation |
|---|---|---|
| `POST` | `/bff/agora/servant/sessions` | `createServantSession` |
| `GET` | `/bff/agora/servant/sessions/{session_id}` | `getServantSession` |
| `POST` | `/bff/agora/servant/sessions/{session_id}/messages` | `postServantSessionMessage` |
| `POST` | `/bff/agora/servant/sessions/{session_id}/terminate` | `terminateServantSession` |
| `GET` | `/bff/agora/servant/sessions/{session_id}/stream` | `streamServantSession` |

The create schema is:

```yaml
ServantSessionCreateRequest:
  type: object
  properties:
    intent:
      type: string
    strategy_ref:
      type: string
    metadata:
      type: object
      additionalProperties: true
  additionalProperties: false
```

That means a frontend cannot send `session_type` in a contract-compliant
request today. The same is true for `sessionType`; it would also be rejected by
the schema. This conflicts with the parent acceptance requirement and the
OpenClaw skill envelope, which includes `session_type` values such as
`interactive`, `trainer`, and `research_task`.

The strategy-dialogue skill adds another constraint: it explicitly allows only
`interactive` and `trainer`. The parent still requires `research_task`, so the
parent must identify which OpenClaw skill/session kind handles that type before
implementation.

## 5. Route Family Decision

The prior packets focused on `/bff/agora/sessions` because that was the route
surface already implemented in `main.py`. Current canonical material now points
AG-BE-ID-003 at `/bff/agora/servant/sessions`.

The parent owner should freeze one of these paths before coding:

| Decision | Consequence |
|---|---|
| Use `/bff/agora/servant/sessions` as canonical for AG-BE-ID-003 | Implement the v1.1 route family in the Agora servant package, resolve `session_type`, and leave legacy `/bff/agora/sessions` as compatibility surface. This matches the OpenAPI v1.1 route family and the parent blocker. |
| Continue using legacy `/bff/agora/sessions` | The parent must first close the existing followup-2 gaps: remove the silent `quick_ask` default, add terminate, add session-scoped SSE, add section 8.2 audit fields, and resolve the split with `/bff/agora/ask/sessions`. |
| Support both as aliases | The parent must define which route is authoritative for create/message/terminate/stream, and which one frontend clients should use in strict live mode. |

Recommended sidecar position: use `/bff/agora/servant/sessions` as the parent
implementation target, because it is the route family in `agora_v1_1.openapi.yaml`
and it includes terminate and stream endpoints. Keep the legacy session routes
out of the first AG-BE-ID-003 implementation except for compatibility decisions
the parent explicitly owns.

## 6. Legacy Surface Re-Check

The legacy gaps from followup-2 still matter if the parent chooses to keep or
alias `/bff/agora/sessions`:

| Legacy issue | Current observation |
|---|---|
| Missing terminate route | No `POST /bff/agora/sessions/{sessionId}/terminate` handler exists in `main.py`. |
| `quick_ask` default | `read_store.create_agora_session()` and `POST /bff/agora/sessions` still default mode to `quick_ask` when no `mode` or `sessionType` is supplied. |
| Parallel ask route | `/bff/agora/ask/sessions` still hardcodes and filters `mode == "quick_ask"`. |
| SSE scope | `GET /bff/sse/agora/sessions/{sessionId}` still delegates to `stream_ask_events()` and does not use `sessionId`. |
| Audit fields | Legacy session responses do not systematically carry `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` in response meta. |
| Error code | `OPENCLAW_UPSTREAM_DEGRADED` is not present in BFF models or session handlers; the servant ensure route currently maps OpenClaw sync errors to `DEPENDENCY_UNAVAILABLE`. |

These observations do not require the sidecar to change code. They are parent
absorption gates if legacy routes remain in scope.

## 7. Frontend Handoff Update

Until the parent contract decision is resolved, `AG-FE-ID-001` and execute-plans
must not wire a live session create UI to either route family.

### Blocked UI Gates

| Frontend capability | Blocked by |
|---|---|
| Create interactive session | No contract-compliant place to send or derive `session_type: interactive` for `POST /bff/agora/servant/sessions`. |
| Create trainer session | Same `session_type` blocker; strategy-dialogue allows this type but OpenAPI create request cannot express it. |
| Create research task session | `research_task` owner skill/session mapping is not frozen; strategy-dialogue does not list it as allowed. |
| Send servant session message | No BFF implementation of `/bff/agora/servant/sessions/{session_id}/messages` exists yet. |
| Terminate servant session | OpenAPI route exists, but BFF implementation is absent. |
| Stream servant session events | OpenAPI route exists at `/stream`, but BFF implementation is absent; legacy SSE alias is not session-scoped. |
| Display OpenClaw degradation | Acceptance requires `OPENCLAW_UPSTREAM_DEGRADED`, but current BFF error enum uses `DEPENDENCY_UNAVAILABLE`. |

### Recommended execute-plans Client Shape After Parent Decision

If the parent confirms `/bff/agora/servant/sessions` as canonical, frontend
clients should wait for a typed contract equivalent to:

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

That client shape is intentionally not a claim that the schema is ready. The
parent must first decide whether `sessionType` is part of the public request,
derived server-side, or represented by separate route/action context.

## 8. Updated Parent Absorption Gates

| Gate | Required parent decision or implementation |
|---|---|
| G0 upstream servant | Confirm AG-BE-ID-002 merged `POST /bff/agora/servant/ensure` is the composition source for session profile and OpenClaw agent metadata. |
| G1 session type contract | Decide how `interactive`, `trainer`, and `research_task` are represented in `ServantSessionCreateRequest` or derived by BFF. Do not implement by accepting undeclared request fields. |
| G2 research task mapping | Name the OpenClaw skill/session kind for `research_task`; strategy-dialogue only allows `interactive` and `trainer`. |
| G3 route family | Freeze `/bff/agora/servant/sessions` vs legacy `/bff/agora/sessions` vs dual alias behavior. |
| G4 package placement | Implement without overwriting AG-BE-ID-002 ensure behavior. Prefer package router ownership under the Agora servant/session surface rather than adding more logic to `main.py`. |
| G5 audit fields | Every create/message/terminate/read/stream operation must carry `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` in audit trail and response meta. |
| G6 degraded error | Decide whether to add `OPENCLAW_UPSTREAM_DEGRADED` as a BFF error code or map it explicitly to the existing error envelope without losing the acceptance code. |
| G7 SSE | Implement session-scoped servant session stream at the chosen route; do not reuse the shared ask-channel alias as proof. |
| G8 capability/OpenAPI alignment | Reconcile `capability_manifest.json` legacy session prefixes with the v1.1 servant session route family before claiming frontend live-mode readiness. |
| G9 tests | Cover create for all approved types, invalid/missing type rejection or derivation, message post, terminate, session-scoped stream, audit meta, idempotency, and OpenClaw degradation. |

## 9. Safe Operator Journey

This is the safe journey once G1-G3 are resolved and the BFF implementation
lands:

1. Operator calls `GET /bff/agora/me` to resolve scope and allowed
   capabilities.
2. Operator calls `POST /bff/agora/servant/ensure`; BFF returns an active
   servant profile for the current tenant/user scope.
3. Operator creates a servant session through the parent-approved route with a
   parent-approved representation of `interactive`, `trainer`, or
   `research_task`.
4. BFF stores a servant session reference, resolved OpenClaw session reference,
   and section 8.2 audit fields.
5. Operator posts messages to the servant session; BFF forwards or schedules
   the OpenClaw session call and records an idempotent audit trail.
6. Frontend connects to the session-scoped SSE route and receives only events
   for that servant session.
7. If OpenClaw is unavailable, BFF returns a 503 carrying the accepted
   degradation code and does not silently create a local-only successful
   session.
8. Operator terminates the session; BFF persists terminal status and publishes
   a terminal session event.

Until G1-G3 are resolved, the safe frontend journey is read-only: identity,
capabilities, and servant ensure may be displayed, while create/message/stream/
terminate controls remain disabled.

## 10. Verification Run

Commands run for this packet:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-ID-002
rg -n "ServantSessionCreateRequest|servant/sessions|session_type|interactive|trainer|research_task|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json
rg -n "session_type|interactive|trainer|research_task" docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md
rg -n "servant/sessions|createServantSession|OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/bff services/control-plane/bff/tests services/control-plane/bff/test_*.py
```

Results:

- Branch was correct: `task/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`.
- Parent `AG-BE-ID-003` is blocked on the `session_type` contract decision.
- Upstream `AG-BE-ID-002` is done and merged into dev.
- OpenAPI v1.1 defines servant session routes but not a create-session type
  field.
- BFF has no `servant/sessions` implementation or tests today.
- Legacy session routes remain in `main.py` and still have the followup-2
  caveats.

## 11. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document was edited.
- No OpenAPI, capability manifest, BFF runtime, router, registry, governance,
  migration, schema, or frontend implementation was changed.
- The only intended authored artifact is this file:
  `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`.

## 12. Reviewer Handoff

Reviewer: `Codex2`

Please review this followup packet for:

1. whether the `ServantSessionCreateRequest` blocker is accurately described
2. whether `/bff/agora/servant/sessions` is correctly separated from the legacy
   `/bff/agora/sessions` and `/bff/agora/ask/sessions` surfaces
3. whether the `research_task` mapping caveat is fair given the strategy-dialogue
   skill allows only `interactive` and `trainer`
4. whether the frontend gate update is actionable for `AG-FE-ID-001`
5. support-only scope compliance

Suggested approval command after review:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh approve AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 "Followup-3 sidecar packet approved; servant-session contract blocker and frontend gates are accurate; support-only scope confirmed."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 "Describe the exact correction needed."
```

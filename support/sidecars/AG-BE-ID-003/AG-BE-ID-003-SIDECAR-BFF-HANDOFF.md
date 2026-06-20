# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet

**Sidecar task:** `AG-BE-ID-003-SIDECAR-BFF-HANDOFF`
**Helper parent:** `AG-BE-ID-003` — Interactive/trainer/research session BFF facade
**Helper kind:** `bff_handoff_packet`
**Parent owner / reviewer:** `Claude` / `Codex`
**Sidecar owner / reviewer:** `Claude` / `Claude2`
**Date:** `2026-06-20`
**Status:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, core contract truth, BFF runtime behavior, registry,
> governance implementation, or execute-plans frontend implementation.

---

## 1. Purpose

This packet gives the `AG-BE-ID-003` parent owner and reviewer a compact BFF
handoff for the interactive/trainer/research session facade. It records:

1. the locally observed session BFF route surface and current implementation state
2. the BFF query gaps blocking a complete AG-BE-ID-003 implementation
3. the required §8.2 audit field contract
4. the operator journey the parent owner must implement
5. frontend handoff targets for downstream `AG-FE-ID-001`
6. review and absorption gates for the parent task

This packet is not a parent implementation and does not approve or reopen the
parent task.

---

## 2. Sources Used

| Source | Why it matters |
|---|---|
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent task definition, owner/reviewer, dependencies, acceptance text |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | Frozen Agora v1 route catalog, §5.3/§17.1 session model, §8.2 audit fields |
| `services/control-plane/bff/main.py` | Current session route implementations (all agora session routes live here) |
| `services/control-plane/bff/agora/identity/router.py` | Migration placeholder — empty router with migration note listing session routes still in main.py |
| `services/control-plane/bff/agora/servant/router.py` | Upstream servant ensure route (AG-BE-ID-002); session facade should compose with this |
| `services/control-plane/specs/agora/strategy_workshop.schema.json` | Required schema artifact for AG-BE-ID-003 |
| `services/control-plane/bff/test_trn002_trainer_session_contract.py` | Trainer session type usage in existing tests |
| `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF.md` | Downstream FE handoff that depends on AG-BE-ID-003 |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-ACCEPTANCE.md` | Upstream servant sidecar; AG-BE-ID-003 depends on servant provisioning being live |

---

## 3. Parent Task Boundary

Parent `AG-BE-ID-003` is defined in the 2026-06-20 dispatch script as:

| Field | Value |
|---|---|
| Parent task | `AG-BE-ID-003` |
| Title | Interactive/trainer/research session BFF facade |
| Owner / reviewer | `Claude` / `Codex` |
| Phase | `EPIC AGORA-ID / Phase 1` |
| Dependencies | `AG-BE-ID-002` |
| Artifacts | `services/control-plane/bff/agora/servant.py`, `services/control-plane/specs/agora/strategy_workshop.schema.json` |

Parent acceptance summary from dispatch:

- can create interactive/trainer/research session and exchange messages
- stream returns via SSE
- every read/write operation carries §8.2 audit fields
  (`trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, `session_id`)
- OpenClaw degradation returns `OPENCLAW_UPSTREAM_DEGRADED`

---

## 4. BFF Surface Observed Locally

### 4.1 Routes implemented in `main.py` (partial — gaps noted in §5)

All Agora session routes currently live in `services/control-plane/bff/main.py`.
`services/control-plane/bff/agora/identity/router.py` is an empty placeholder
that explicitly lists these routes as "migration pending":

| Route | Current status | Notes |
|---|---|---|
| `GET /bff/agora/sessions` | Implemented | Lists sessions from read store |
| `POST /bff/agora/sessions` | Implemented, partial | Accepts `sessionType`/`mode` as payload fields; no enum enforcement; no OpenClaw mapping; no §8.2 audit fields |
| `GET /bff/agora/sessions/{sessionId}` | Implemented | Returns session detail; no audit fields in response |
| `GET /bff/agora/sessions/{sessionId}/messages` | Implemented | Lists messages for session |
| `POST /bff/agora/sessions/{sessionId}/messages` | Implemented, partial | Appends message; no §8.2 audit fields |
| `GET /bff/sse/agora/sessions/{sessionId}` | Implemented, partial | Aliased to `ask` channel stream, not session-specific |
| `POST /bff/agora/messages/{messageId}/actions/{actionId}` | Implemented | Message endorsement/rejection/flagging |

### 4.2 Routes absent from BFF entirely

| Route | Status | Owner |
|---|---|---|
| `POST /bff/agora/sessions/{sessionId}/terminate` | Not implemented | AG-BE-ID-003 |

### 4.3 Artifact `services/control-plane/bff/agora/servant.py` is missing

The dispatch artifact list names `services/control-plane/bff/agora/servant.py`
as the home for the AG-BE-ID-003 session facade logic. This file does not exist.
`services/control-plane/bff/agora/servant/router.py` is owned by AG-BE-ID-002
(servant ensure). AG-BE-ID-003 must not overwrite or merge into the servant
ensure router; it needs either a new `agora/session/` subpackage or a separate
module that does not conflict with the servant router path.

### 4.4 Strategy workshop schema exists

`services/control-plane/specs/agora/strategy_workshop.schema.json` is present
and covers `StrategyWorkshop` v1.0 (status lifecycle: `open`, `in_review`,
`concluded`, `archived`; subject kinds: `strategy_spec`, `research_plan`,
`candidate_artifact`, `free_form`). Parent owner should validate session
responses for `session_type: research_task` against this schema or an inline
equivalent.

---

## 5. BFF Query Gaps

### G1 — Terminate route missing

`POST /bff/agora/sessions/{sessionId}/terminate` does not exist in the BFF.
Without it, operators cannot close interactive or research sessions in a
structured way, and the SSE stream for that session cannot be signaled to end.

**Required:** parent owner must add this route. The route must:
- validate session ownership (same `user_id`/`tenant_id` predicate as session creation)
- transition session status to `concluded` or `terminated`
- include §8.2 audit fields in the response
- publish a stream event so connected SSE clients see the terminal state

### G2 — Session type classification and OpenClaw mapping absent

`POST /bff/agora/sessions` today accepts `sessionType`/`mode` from the payload
but performs no validation. There is no enforcement of the three session types
(`interactive`, `trainer`, `research_task`) from SD §5.3, and no OpenClaw
session mapping is invoked.

**Required:** the parent owner must:
- validate `session_type` as one of `interactive`, `trainer`, `research_task`;
  reject with 422 for unknown values
- map `session_type` to the correct OpenClaw session kind via the servant agent
  (from the provisioned `ServantProfile` OpenClaw agent ref returned by AG-BE-ID-002)
- store the resolved OpenClaw session ref in the session record for later message routing

### G3 — §8.2 audit fields absent from session routes

SD §8.2 requires every session read/write operation to carry:

| Field | Source |
|---|---|
| `trace_id` | `X-Trace-Id` header or generated |
| `request_id` | `X-Request-Id` header or generated |
| `actor_id` | identity operator ID |
| `user_id` | Agora user ID from scope |
| `persona_id` | servant persona ID |
| `session_id` | session being acted on |

None of the current session routes in `main.py` systematically include these
fields. They are present in other BFF write routes (command engine, governance)
but have not been applied to the Agora session surface.

**Required:** the parent owner must thread these fields through all session
write routes (`POST /bff/agora/sessions`, `POST .../messages`,
`POST .../terminate`) and include them in audit trail entries and response meta.

### G4 — OPENCLAW_UPSTREAM_DEGRADED not implemented

When the OpenClaw agent backing a servant session is unavailable, the session
BFF facade must return `OPENCLAW_UPSTREAM_DEGRADED` error code (per acceptance
criteria). This error code does not appear in any current session route handler.

**Required:** the parent owner must handle `Exception` from any OpenClaw call
in session routes and surface a structured 503 with error code
`OPENCLAW_UPSTREAM_DEGRADED` and a degradation reason. The behavior should
match the `DEPENDENCY_UNAVAILABLE` pattern already used in
`agora/servant/router.py` for OpenClaw sync failures.

### G5 — SSE stream is not session-scoped

`GET /bff/sse/agora/sessions/{sessionId}` is registered in `main.py` but is
aliased directly to `stream_ask_events()` — the ask-channel SSE stream. The
`sessionId` path parameter is received but not used; all callers receive the
same shared ask-channel event stream regardless of which session they specify.

**Required:** the parent owner must either:
- implement a per-session SSE channel keyed to `sessionId`; or
- document explicitly that the current ask-channel alias is the intended
  behavior and explain why per-session filtering is not needed

Either path must be a deliberate decision, not a silent alias.

### G6 — Migration from `main.py` to package module

`agora/identity/router.py` documents that all session routes must migrate from
`main.py` to the package router once downstream tasks are ready. AG-BE-ID-003
is the first task that targets new session behavior. If the parent owner
adds session type logic, audit fields, and terminate to `main.py` without
migrating to the package module, future tasks will continue to layer on an
already-overloaded `main.py`.

**Recommended:** perform the migration as part of AG-BE-ID-003; add session
routes (including the new ones) to a new `agora/session/router.py` package
module and register it in the BFF package router alongside the existing
`agora/servant/router.py`. Remove the corresponding stubs from `main.py`.

---

## 6. Operator Journey for Parent Implementation

| Step | BFF call | Expected behavior |
|---|---|---|
| 1. Servant already provisioned | Prior AG-BE-ID-002 | `ServantProfile` active for this `(tenant_id, user_id)` pair |
| 2. Create interactive session | `POST /bff/agora/sessions` with `session_type: interactive` | 201; session record with OpenClaw session ref; §8.2 audit fields in response |
| 3. Create trainer session | `POST /bff/agora/sessions` with `session_type: trainer` | 201; mapped to trainer OpenClaw session kind |
| 4. Create research session | `POST /bff/agora/sessions` with `session_type: research_task` | 201; session bound to research_task type; response validated against StrategyWorkshop schema or equivalent |
| 5. Send message | `POST /bff/agora/sessions/{sessionId}/messages` with §8.2 headers | 201; message stored and routed to OpenClaw; SSE event published |
| 6. Stream events | `GET /bff/sse/agora/sessions/{sessionId}` | SSE stream scoped to this session; receives message acknowledgement and persona response events |
| 7. OpenClaw degraded | Any session write | 503 with `OPENCLAW_UPSTREAM_DEGRADED` error code |
| 8. Terminate session | `POST /bff/agora/sessions/{sessionId}/terminate` | 200; session status → `concluded`; SSE terminal event |

---

## 7. Frontend Handoff Targets for AG-FE-ID-001

`AG-FE-ID-001` depends on `AG-BE-ID-003` and cannot enable command/session UI
until the session facade is live. Per the `AG-FE-ID-001` sidecar packet:

> _"keep command-line/session UI as a disabled skeleton unless the parent can
> verify AG-BE-ID-003 routes are ready"_

### 7.1 What the frontend may consume today (before AG-BE-ID-003 lands)

| Surface | Current state | Frontend handling |
|---|---|---|
| `GET /bff/agora/sessions` | Implemented | May list sessions; no session_type enforcement |
| `GET /bff/agora/sessions/{sessionId}` | Implemented | May read session detail; no audit fields |
| `GET /bff/agora/me` | Implemented (AG-BE-ID-001) | Identity check; servant policy available |
| `POST /bff/agora/servant/ensure` | Implemented (AG-BE-ID-002) | Servant provisioning live |

### 7.2 Frontend gates blocked on AG-BE-ID-003

| Gate | Blocked until |
|---|---|
| Command-line / session create UI | `POST /bff/agora/sessions` with validated `session_type` |
| Session message UI | `POST .../messages` with §8.2 audit fields required in request |
| Session terminate button | `POST .../terminate` exists and returns 200 |
| Live session event stream | Session-scoped SSE stream (not ask-channel alias) |
| OpenClaw degradation indicator | `OPENCLAW_UPSTREAM_DEGRADED` 503 path reachable |

### 7.3 Recommended session client shape for execute-plans

Once AG-BE-ID-003 lands, the frontend implementation under
`execute-plans/src/lib/bff-v1/agora/` should expose:

- `createAgoraSession(sessionType: 'interactive' | 'trainer' | 'research_task', title: string): Promise<SessionEnvelope>` — calls `POST /bff/agora/sessions` with validated type; includes required `X-Request-Id` and `X-Trace-Id` headers; maps 422 to a typed `session_type_invalid` error
- `sendSessionMessage(sessionId: string, content: string): Promise<MessageEnvelope>` — calls `POST /bff/agora/sessions/{sessionId}/messages` with §8.2 audit headers
- `terminateSession(sessionId: string): Promise<void>` — calls `POST /bff/agora/sessions/{sessionId}/terminate`
- `streamSessionEvents(sessionId: string): EventSource` — connects to session-scoped SSE once G5 gap is resolved; until then surfaces a `session_stream_unavailable` state

None of these clients should be wired to live in strict mode until the parent
owner confirms the routes are landed and tested.

---

## 8. Verification Commands

Commands run by this sidecar to assess current BFF state:

```bash
# Confirm session routes exist in main.py
grep -n "@app.get\|@app.post" services/control-plane/bff/main.py | grep "agora/sessions\|agora/ask"

# Confirm terminate route is missing
grep -rn "terminate" services/control-plane/bff/ --include="*.py"

# Confirm identity/router.py is an empty placeholder
cat services/control-plane/bff/agora/identity/router.py

# Confirm servant.py artifact does not exist
test -f services/control-plane/bff/agora/servant.py && echo EXISTS || echo MISSING

# Confirm strategy_workshop schema exists
test -f services/control-plane/specs/agora/strategy_workshop.schema.json && echo EXISTS

# Confirm OPENCLAW_UPSTREAM_DEGRADED is absent from session routes
grep -rn "OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/bff/
```

Findings:
- `POST /bff/agora/sessions/{sessionId}/terminate` — **absent**
- `services/control-plane/bff/agora/servant.py` — **absent** (artifact target for AG-BE-ID-003)
- `services/control-plane/bff/agora/identity/router.py` — **empty placeholder**
- `services/control-plane/specs/agora/strategy_workshop.schema.json` — **present**
- `OPENCLAW_UPSTREAM_DEGRADED` — **absent** from all session route handlers

---

## 9. Parent Absorption Gates

The parent owner should not hand `AG-BE-ID-003` to review as complete until all
applicable gates are satisfied or explicitly scoped down:

| Gate | Pass condition |
|---|---|
| G1 session_type | `POST /bff/agora/sessions` validates `session_type` as `interactive`, `trainer`, or `research_task`; rejects unknown values with 422 |
| G2 OpenClaw mapping | `POST /bff/agora/sessions` resolves the servant's OpenClaw agent ref and maps session type to OpenClaw session kind |
| G3 terminate route | `POST /bff/agora/sessions/{sessionId}/terminate` returns 200 with session status `concluded` and publishes SSE terminal event |
| G4 §8.2 audit fields | All session write routes include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, and `session_id` in audit trail and response meta |
| G5 OPENCLAW_UPSTREAM_DEGRADED | Any OpenClaw failure in session routes returns 503 with `OPENCLAW_UPSTREAM_DEGRADED` error code |
| G6 SSE scope | `GET /bff/sse/agora/sessions/{sessionId}` delivers events scoped to that session (not a shared channel alias) or the alias behavior is explicitly documented |
| G7 module migration | Session facade logic lives in `services/control-plane/bff/agora/servant.py` or a new `agora/session/` package module; `main.py` session handlers are removed or replaced |
| G8 schema compliance | `session_type: research_task` session response is compatible with `strategy_workshop.schema.json` or a documented inline schema |
| G9 tests | Tests cover session_type 422 rejection, successful create for all three types, message post with audit fields, terminate, OpenClaw degradation 503, and SSE stream behavior |

---

## 10. Dependency Context

```
AG-BE-ID-001 (user scope + servant policy)
  └──> AG-BE-ID-002 (servant ensure/provision)
         └──> AG-BE-ID-003 (session BFF facade)  ← this task
                └──> AG-FE-ID-001 (Agora shell, command-line skeleton)
                └──> AG-TEST-ID-001 (isolation E2E)
```

AG-BE-ID-003 depends on AG-BE-ID-002 being live because:
- the session facade must look up the servant's `ServantProfile` and OpenClaw
  agent ref to route session creation to the correct OpenClaw session kind
- cross-user isolation checks in session routes use the same `(tenant_id, user_id)`
  scope resolution from AG-BE-ID-001

Until `POST /bff/agora/servant/ensure` returns a 200 `ServantProfile`,
AG-BE-ID-003 session routes cannot resolve the agent mapping needed to create
an OpenClaw-backed session.

---

## 11. Reviewer Handoff

Reviewer: `Claude2`

Please review this sidecar for:

1. support-only scope compliance (no BFF code or schema changed)
2. accurate classification of which session routes are implemented vs. missing
3. correct identification of the five BFF query gaps (G1–G5) plus the module
   migration gap (G6)
4. accuracy of the §8.2 audit field contract as described
5. usefulness of the operator journey and frontend handoff targets for
   AG-BE-ID-003 parent owner and AG-FE-ID-001 team

Suggested approval command:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py approve AG-BE-ID-003-SIDECAR-BFF-HANDOFF "Sidecar BFF/frontend handoff packet approved; support-only artifact documents AG-BE-ID-003 session BFF gaps, audit fields, operator journey, and FE handoff gates."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py reopen AG-BE-ID-003-SIDECAR-BFF-HANDOFF "Describe the exact packet correction needed."
```

> **Review outcome:** Approved by `Claude2` on 2026-06-20. Review notes: support-only scope confirmed; G1–G6 BFF gaps correctly identified; §8.2 audit field contract correct; operator journey and FE handoff targets complete.

---

## 12. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document has been edited.
- No `main.py`, BFF router, session schema, registry, governance, or
  frontend implementation was changed.
- No `strategy_workshop.schema.json` or any other spec file was modified.
- The intended sidecar artifact is this file:
  `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF.md`.

*Prepared by Claude for the `AG-BE-ID-003-SIDECAR-BFF-HANDOFF` support slice.*

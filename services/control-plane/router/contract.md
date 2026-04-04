# Control Plane Router Contract

**Task:** P4-001  
**Owner:** Claude  
**Reviewer:** Codex  
**Status:** v1 LOCKED (Codex review fixes applied 2026-04-02)  

---

## 1. Responsibility

The Router is the single entry point for all inbound messages to the control plane.
It does not own business logic; it owns dispatch rules.

```
Channel (Telegram / Discord / Web / Console)
        │  POST /route { channel, user_id, message }
        ▼
      Router  ─── permission check (OC-001 allowlist) ──→ reject if denied
        │
        ├─ intent → research  ──→ Persona Agent → ResearchTool → Worker
        ├─ intent → execution ──→ Persona Agent → ExecutionTool → SignalStore → LEAN
        ├─ intent → status    ──→ Persona Agent → StatusTool → ai-status.json
        └─ intent → monitor   ──→ Persona Agent → MonitoringTool → Monitoring Event Stream
```

---

## 2. HTTP API

### POST /route

**Request:**
```json
{
  "channel":    "telegram | discord | web | console",
  "user_id":    "<string>",
  "message":    "<natural language string>",
  "session_id": "<uuid, optional — generated if absent>"
}
```

**Response (v1 — aligned with router/main.py RouteResponse):**
```json
{
  "session_id":  "<uuid>",
  "agent_id":    "<string>",
  "response":    "<string>",
  "intent":      "<string | null>",
  "skill":       "<string | null>",
  "permission":  "allow | allow_with_approval"
}
```

> **Note:** `monitoring` field and `GET /stream/{session_id}` were in the original draft but are
> NOT implemented in v1. They are deferred to a follow-up task once MonitoringEvent infrastructure
> is in place (depends on FB-003). Do not build downstream consumers against these fields yet.

**Error responses:**

| HTTP status | Meaning |
|---|---|
| 400 | Missing required field |
| 403 | Tool permission denied (OC-001 denylist) |
| 503 | Persona Agent unavailable |

---

## 3. Dispatch Rules

The Router selects a dispatch target based on:
1. `channel` — controls which tool permissions apply (console > web > telegram/discord)
2. classified `intent` — returned by Persona Agent's `intent_classify` node
3. `persona_policy` — loaded from OC-001 allowlist for this user/channel combination

### Intent → Tool mapping

| Intent class | Tool invoked | Requires promotion gate |
|---|---|---|
| `research.*` | QlibTool / VectorbtTool / FinRLTool / QuantLibTool | No (paper only) |
| `execution.signal` | ExecutionTool → SignalStore write | Yes — artifact must be `paper` or `live` |
| `execution.status` | StatusTool | No |
| `monitor.pnl` | MonitoringTool | No |
| `monitor.positions` | MonitoringTool | No |
| `governance.approve` | GovernanceTool | Yes — operator role required |
| `unknown` | Persona Agent fallback response | No |

### Governance constraint (from OC-001 review)

Any tool call that reaches LEAN directly (i.e., `ExecutionTool` → SignalStore → LEAN) must:
- check that the signal artifact's promotion state is `paper` or `live`
- reject `candidate` or `draft` artifacts with HTTP 403 and log to audit trail
- the Router must NOT allow this constraint to be bypassed via channel parameter

---

## 4. Monitoring Handoff — DEFERRED (not v1 scope)

> **This section describes the intended future design, not the current v1 contract.**
> `MonitoringEvent` emission, SSE stream (`GET /stream/{session_id}`), and Operator Console
> integration are all deferred until FB-003 (execution telemetry capture) is complete.
> Do not build downstream consumers against these interfaces yet.

Planned event schema: `services/control-plane/router/monitoring_event_schema.json`

When implemented, the intended consumers will be:

| Consumer | Delivery mechanism | Purpose |
|---|---|---|
| Operator Console | SSE stream (`GET /stream/{session_id}`) | Live PnL, positions, alerts |
| Persona Agent | Internal tool call | Surface alerts via channel |
| Evolution Plane (FB-003) | Async subscriber | Telemetry for feedback store |

---

## 5. Session and State

- `session_id` is a UUID created by Router on first request; echoed on subsequent requests
- Session state (message history, persona context) is owned by Persona Agent, not Router
- Router is stateless between requests; all state lives in Persona Agent (Redis) or audit log (Firestore)

---

## 6. Session and Rate-Limit Policy (v1 locked)

| Parameter | Value | Notes |
|---|---|---|
| Session TTL | 24h idle | Timer resets on each successful `/route` response |
| Expired session | 404 / 410 | Client must create new session |
| Rate limit — telegram/discord | 20 req/min/user | |
| Rate limit — web | 60 req/min/user | |
| Rate limit — console | 120 req/min/operator | |
| Rate limit — execution.*/governance.* | 5 req/min/user | Applies on top of channel limit |

Enforcement: rate limits are the responsibility of the API gateway in production.
The policy values above are canonical and must be respected in all implementations.

## 7. Permission Evaluation Flow (implemented in main.py)

```
1. classify intent (local keyword classifier → Persona intent_classify when wired)
2. _evaluate_permission(channel, intent) → allow | deny | allow_with_approval
3. if deny → 403 immediately, Persona is never called
4. if allow_with_approval → dispatch to Persona, response includes permission="allow_with_approval"
                            downstream Governance service must intercept and hold for approval
5. if allow → dispatch to Persona normally
```

The critical governance invariant: **permission evaluation always precedes any side-effectful dispatch.**

## 8. Persona Agent Designation (OSS Integration Model)

The router dispatches to a Persona Agent at `PERSONA_URL` (default `http://localhost:8002`).

**Current status of the Persona Agent (`services/control-plane/persona/main.py`):**

> The persona agent is currently a **local stub surrogate**.
> Every LangGraph node (`intent_classify`, `skill_select`, `memory_lookup`, `respond`)
> returns placeholder values. The agent responds with
> `"[system not ready — upstream schemas not locked]"` to all `/invoke` requests.
> LangGraph is imported conditionally and falls back to a stub mode if not installed.

**Designation decision (recorded per AUD-CLAUDE-001):**

The local stub is a **temporary placeholder**, not a permanent local reimplementation of upstream OpenClaw.

| Path | Condition | Required work |
|---|---|---|
| Integrate upstream OpenClaw | If OpenClaw provides a Python SDK or REST API for persona orchestration | Select upstream source, pin version, build adapter from router's `/invoke` call to OpenClaw API |
| Keep local agent as permanent surrogate | If upstream OpenClaw does not cover persona agent functionality | Formally document local-surrogate designation in `OSS_INTEGRATION_CHECKLIST.md`; wire LangGraph + LLM backend; remove stub placeholders |

**This decision is deferred and must be made before OC-002 (cron workflows) begins.**

Until the decision is made, the router contract is valid — the dispatch interface (`POST PERSONA_URL/invoke`) is stable regardless of which path is chosen.

---

## 9. Remaining Open Items

- [ ] Persona agent path decision: upstream OpenClaw integration vs. permanent local surrogate (blocks OC-002)
- [ ] Full policy-object loading from storage backend (OC-001 §9 — depends on storage choice)
- [ ] Approval workflow service (intercepts `allow_with_approval` responses)
- [ ] MonitoringEvent stream (`GET /stream/{session_id}`) — deferred until FB-003
- [ ] Per-user session store (currently stateless — session_id generated fresh each call)

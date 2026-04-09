# OpenClaw Integration — Pin and Adapter Boundary

Last updated: 2026-04-10
Owner: OSS-001 (Qwen)
Reviewer: Codex
Status: locked v1 pin
Upstream: https://github.com/openclaw/openclaw

## 1. Pinned Upstream Release

| Field | Value |
|---|---|
| **Repository** | `https://github.com/openclaw/openclaw` |
| **Release tag** | `v2026.4.7` |
| **Commit SHA** | `5050017` |
| **Release date** | 2026-04-08 |
| **Image reference** | `openclaw/openclaw:v2026.4.7` |
| **Official site** | `https://openclaw.im/` |

### Pin Rationale

- **Stable release**, not a pre-release or beta.
- **2-day stability window** at time of pin (released 2026-04-08, pinned 2026-04-10).
- Selecting `v2026.4.7` instead of `v2026.4.9` (latest) to allow upstream to stabilize before adopting — avoids day-of-release regressions.
- The versioning scheme (`v2026.M.D`) is date-based; we should expect frequent minor releases. Pinning by **tag + commit SHA** ensures immutability.

### Upgrade Policy

When upgrading:
1. Monitor upstream releases for at least **48 hours** before pinning.
2. Re-run the smoke-test suite (§5) against the new pin.
3. Update this file with the new tag, SHA, and date.
4. Record the change in `ai-activity-log.jsonl` with `type: oss_pin_change`.

---

## 2. Integration Mode

**Option C: Run upstream OpenClaw as a separate runtime and adapt its outputs.**

This means:
- OpenClaw runs as an independent service (Docker/Kubernetes).
- Pantheon communicates with it via its external API.
- No vendoring, no source modification, no local rewrite.
- The `openclaw-gateway-adapter` in Pantheon is the sole integration seam.

### Rejected Options

- **Option A (Vendor/submodule):** Rejected — too large a TypeScript runtime, high coupling before clean adapter seam.
- **Option B (Rebuild in LEAN):** Rejected — accidental fork, defeats OSS adoption purpose.

---

## 3. Adapter Boundary

The `openclaw-gateway-adapter` is Pantheon's responsibility boundary. It maps Pantheon domain objects to OpenClaw runtime objects and vice versa.

### 3.1 Adapter Responsibilities

| Responsibility | Direction | Contract |
|---|---|---|
| Agent provisioning | Pantheon → OpenClaw | `create_agent`, `update_agent`, `deactivate_agent` |
| Session lifecycle | Pantheon → OpenClaw | `create_session`, `resume_session`, `terminate_session`, `get_session_status` |
| Tool resolution & invocation | Pantheon → OpenClaw | `resolve_tools`, `invoke_tool`, `list_tools` |
| Skill resolution | Pantheon → OpenClaw | `resolve_skills`, `attach_shared_skill`, `attach_local_skill` |
| Multi-agent consultation | Pantheon → OpenClaw | `spawn_subagent`, `send_session_message`, `collect_replies` |
| Workflow/cron/hooks | Pantheon → OpenClaw | `schedule_job`, `trigger_workflow`, `get_job_status`, `cancel_job` |
| Event telemetry extraction | OpenClaw → Pantheon | Event envelope (§3.3) |
| Error classification | OpenClaw → Pantheon | Three-layer error model (§3.4) |

### 3.2 Runtime Object Mapping

| Pantheon Object | OpenClaw Object | Adapter Mapping |
|---|---|---|
| `Persona` | Agent definition | `persona_id` → `agent_id`, `effective_capability_set` → tool/skill allowlist |
| `CapabilitySnapshot` | Effective tool/skill set | Computed by adapter from Pantheon RBAC, then passed to `create_session` |
| `TeachingSession` | Trainer session | `session_type: "trainer"` |
| `ConsultRequest` | Consult/sub-agent session | Adapter creates consult group, manages message routing |
| `WorkflowTemplate` | Workflow invocation | Adapter serializes Pantheon workflow ref + context into OpenClaw payload |

### 3.3 Event Telemetry Envelope

Every event emitted by the adapter must conform to this shape:

```json
{
  "event_type": "string (see §3.3a)",
  "persona_id": "string",
  "session_id": "string | null",
  "trace_id": "string",
  "request_id": "string | null",
  "actor_type": "persona | operator | system",
  "environment": "paper | canary | live",
  "timestamp": "ISO 8601 (RFC 3339)",
  "payload": "object (event-specific)"
}
```

#### 3.3a Required Event types

Per `OPENCLAW_RUNTIME_CONTRACT.md` §11:

- `agent.created`, `agent.updated`, `agent.deactivated`
- `session.created`, `session.terminated`, `session.degraded`, `session.quarantined`
- `circuit_breaker.opened`, `circuit_breaker.half_open`, `circuit_breaker.closed`
- `consult.spawned`
- `workflow.triggered`
- `cron.job.started`, `cron.job.failed`
- `tool.invoked`, `tool.denied`
- `error.unknown_upstream`

### 3.4 Error Classification

Per `OPENCLAW_RUNTIME_CONTRACT.md` §9:

| Layer | Examples | Adapter Action |
|---|---|---|
| **Known typed errors** | `AGENT_NOT_FOUND`, `SESSION_NOT_FOUND`, `CAPABILITY_DENIED`, `TOOL_INVOCATION_FAILED`, etc. | Map to Pantheon error domain, retry if `retryable: true` |
| **Transport/system errors** | `NETWORK_PARTITION`, `OOM_KILL`, `CONNECTION_REFUSED`, etc. | Exponential backoff, circuit breaker if persistent |
| **unknown_upstream_error** | Anything not mappable | §3.4a fallback |

#### 3.4a unknown_upstream_error Fallback

1. **Preserve raw envelope** — raw payload, stderr/stdout, response code, context.
2. **Return safe degraded result** — `consult_unavailable`, `preview_unavailable`, `review_assist_unavailable`, or deny-by-default.
3. **Isolate agent runtime** — mark session `degraded`, trip circuit breaker on repeat.
4. **Escalate as incident** — 3 consecutive unknown errors within 5 minutes → open incident.

---

## 4. Security and Isolation Requirements

| Requirement | Enforcement |
|---|---|
| Per-agent workspace | Adapter ensures unique `workspace_ref` per persona |
| Per-agent auth profile | No credential sharing across persona sessions |
| No shared agentDir | Validated at provisioning time |
| Secret isolation | Broker secrets never exposed to OpenClaw global scope |
| Capability filtering | Pantheon RBAC evaluated before any tool/skill resolution call |
| Audit trail | Every session has `persona_id`, `session_id`, `trace_id`, `request_id`, `actor_type`, `environment` |

---

## 5. Smoke-Test Overview

See `smoke_test.md` for the full executable plan.

Minimal proof required:
1. Pinned upstream runtime can start (Docker)
2. One minimal approved workflow can be invoked
3. That workflow emits a governed handoff payload
4. The payload normalizes into local `StrategySpec`
5. The normalized output validates against local schema

---

## 6. Adapter Facade API

The adapter exposes these endpoints to Pantheon internal services:

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/control/personas/{persona_id}/sessions` | Create session for persona |
| `POST` | `/control/sessions/{session_id}/invoke` | Invoke tool/session action |
| `POST` | `/control/consult/spawn` | Spawn sub-agent consultation |
| `GET` | `/control/sessions/{session_id}` | Query session status |
| `GET` | `/control/personas/{persona_id}/capabilities` | Resolve effective capabilities |
| `POST` | `/control/jobs` | Schedule workflow/cron job |
| `GET` | `/control/jobs/{job_id}` | Query job status |

---

## 7. Open Questions (Deferred to Implementation)

| Question | Decision Needed By | Owner |
|---|---|---|
| Exact transport (HTTP/gRPC/queue) for adapter ↔ OpenClaw communication | Before adapter implementation | Platform architect |
| Which workflow family is the first production smoke test (research ingest, approval, or deployment) | Before adapter implementation | Platform architect |
| OpenClaw health-check endpoint and readiness probe definition | Before deployment | Platform SRE |
| Rate limiting and quota policy per persona/session | Before production deployment | Platform architect |

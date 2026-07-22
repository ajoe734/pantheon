# Persona Plane Service Contract

**Task:** PER-001
**Owner:** Claude
**Reviewer:** Codex
**Status:** Review-ready
**Phase:** Phase 5 — Persona and Application Surfaces
**Depends on:** CAP-001, RUN-001
**Tier:** L1 Platform Architecture & Policy
**Last updated:** 2026-04-10

---

## 1. Purpose

This document is the **formal service contract** for the Persona Plane in Pantheon.

It adopts the three-layer persona model from `PERSONA_RUNTIME_MODEL.md` into
concrete platform objects with explicit field boundaries, validated lifecycle
transitions, and a formal relationship to the capital and runtime binding
objects defined in CAP-001 and RUN-001.

**PER-001 contract decisions:**

1. `SessionPersona` now carries `runtime_binding_id`, `deployment_stage`, and
   `capital_pool_id` as **first-class contract fields** — not bundle convention.
   (Resolves Codex PER-001A review follow-up §7.1.)

2. The canonical naming for the governance edge across the chain is clarified:
   `DeploymentPlan.binding_id` and `RuntimeBinding.persona_capital_binding_id`
   both refer to the same `PersonaCapitalBinding.binding_id`.  Session audit
   resolves this edge via `SessionPersona.runtime_binding_id → RuntimeBinding.
   persona_capital_binding_id`.
   (Resolves Codex PER-001A review follow-up §7.5.)

---

## 2. Objects Defined by This Contract

### 2.1 Persona (registry object — static layer)

Source of truth: `PersonaRegistry`
Schema: `persona_registry.schema.json`
Python object: `Persona` in `persona_registry.py`

**Required fields:**
| Field | Type | Description |
|---|---|---|
| `persona_id` | string | Immutable unique identifier |
| `name` | string | Human-readable display name |
| `mandate` | string | Operating mandate and strategy scope |
| `lifecycle_state` | enum | Governance lifecycle state |
| `created_at` | ISO-8601 | Creation timestamp |

**Policy reference fields (resolved at session time, not carried into bindings):**
| Field | Description |
|---|---|
| `tool_profile_id` | Tool profile reference — resolved into CapabilitySnapshot |
| `route_policy_id` | Route policy reference — resolved into CapabilitySnapshot |
| `consult_policy_id` | Consult policy reference — enforced at session creation |

**Non-responsibilities of Persona registry object:**
- Does NOT carry current runtime state
- Does NOT reference RuntimeBinding directly
- Does NOT carry session or capability data
- Does NOT reference PersonaCapitalBinding (that is the binding's reference to persona)

### 2.2 CapabilitySnapshot (computed at session start)

Source of truth: computed from `route_policy` + `consult_policy` + RBAC
Schema: `capability_snapshot.schema.json`
Python object: `CapabilitySnapshot` in `persona_registry.py`

**Session-bound immutability rule:** Once computed at session start, a
`CapabilitySnapshot` does not change if registry policies change during
the session. The session runs with the capability set that was effective
at the moment it was created.

**Required fields:**
| Field | Type | Description |
|---|---|---|
| `snapshot_id` | string | Unique snapshot identifier |
| `persona_id` | string | Persona this was computed for |
| `generated_at` | ISO-8601 | When computed |

**Capability fields (all optional, default empty):**
| Field | Description |
|---|---|
| `effective_tools` | Tool names/IDs allowed in this session |
| `effective_skills` | Skill names/IDs allowed in this session |
| `effective_workflows` | Workflow names/IDs allowed in this session |
| `restrictions` | Explicit restrictions applied |
| `source_refs` | Policy document refs used to compute this snapshot |

### 2.3 SessionPersona (control layer — session object)

Source of truth: `PersonaSessionStore`
Schema: `session_persona.schema.json`
Python object: `SessionPersona` in `persona_registry.py`

**Required fields:**
| Field | Type | Description |
|---|---|---|
| `session_id` | string | Unique session identifier |
| `persona_id` | string | Persona this session belongs to |
| `session_type` | enum | Session type (see §3) |
| `status` | enum | Session lifecycle status |
| `started_at` | ISO-8601 | Session start time |
| `capability_snapshot_id` | string | Frozen CapabilitySnapshot for this session |

**PER-001 first-class runtime-context fields (nullable):**
| Field | Type | Set when | Description |
|---|---|---|---|
| `runtime_binding_id` | string\|null | Deployment-bound sessions | The active RuntimeBinding at session start |
| `deployment_stage` | string\|null | When `runtime_binding_id` is set | `paper`/`canary`/`live`/`frozen` from RuntimeBinding |
| `capital_pool_id` | string\|null | When `runtime_binding_id` is set | Capital pool this session acts against |

**Consistency rule:** `deployment_stage` and `capital_pool_id` MUST both be
set whenever `runtime_binding_id` is set. Neither may be null while the other
is set.

**Audit fields:**
| Field | Description |
|---|---|
| `trace_id` | Distributed trace identifier |
| `request_id` | Idempotency / correlation identifier |
| `context_bundle_ref` | Reference to session context bundle (replay anchor) |
| `task_ref` | Reference to the task being executed |

---

## 3. Session Types and Runtime Binding Rules

| Session Type | Requires `runtime_binding_id`? | Typical use |
|---|---|---|
| `interactive` | Yes | Operator / persona direct interaction against active deployment |
| `background_job` | Yes | Workflow-triggered background execution against deployment |
| `trainer` | No | Human-in-the-loop persona policy adjustment |
| `research_task` | No | Research ingestion, hypothesis exploration |
| `consult` | No | Peer consultation between personas |
| `committee` | No | Multi-persona committee session |
| `red_team` | No | Adversarial / stress testing session |

---

## 4. Persona Lifecycle Model

### 4.1 States

| State | Deployment sponsorship | New sessions |
|---|---|---|
| `draft` | No | No (uninitialized) |
| `research_only` | No | Research/trainer only |
| `consultable` | No | All non-deployment session types |
| `paper_owner` | Paper only | All session types |
| `live_owner` | Paper/canary/live | All session types |
| `frozen` | No | No new capability expansion |
| `retired` | No | Audit/replay only |

### 4.2 Allowed Lifecycle Transitions

| From | To | Owner |
|---|---|---|
| `draft` | `research_only` | Persona Plane |
| `research_only` | `consultable` | Governance Plane |
| `research_only` | `frozen` | Governance / Evolution |
| `consultable` | `paper_owner` | Governance Plane |
| `consultable` | `frozen` | Governance / Evolution |
| `paper_owner` | `live_owner` | Governance Plane |
| `paper_owner` | `frozen` | Governance / Evolution |
| `live_owner` | `frozen` | Governance / Evolution |
| `live_owner` | `retired` | Governance |
| `frozen` | `research_only` | Governance (after revalidation) |
| `frozen` | `retired` | Governance |

---

## 5. Full Audit Chain

A session that acts against a live deployment must carry the complete audit
chain from persona identity to capital governance:

```
Persona (registry)
    │ persona_id
    ▼
PersonaCapitalBinding                ← governance admissibility (CAP-001)
    │ binding_id
    │ persona_id
    │ capital_pool_id
    │ role + allowed_deployment_scope
    ▼
DeploymentPlan                       ← deployment intent
    │ plan_id
    │ binding_id                     ← references PersonaCapitalBinding.binding_id
    │ artifact_id
    │ target_stage
    ▼
RuntimeBinding                       ← actual running state (RUN-001)
    │ binding_id
    │ plan_id
    │ persona_capital_binding_id     ← same edge as DeploymentPlan.binding_id
    │ runtime_id
    │ capital_pool_id
    │ artifact_id
    │ deployment_mode
    ▼
SessionPersona                       ← session control layer (PER-001)
    │ session_id
    │ persona_id
    │ capability_snapshot_id
    │ runtime_binding_id             ← references RuntimeBinding.binding_id
    │ deployment_stage               ← = RuntimeBinding.deployment_mode
    │ capital_pool_id                ← = RuntimeBinding.capital_pool_id
    │ context_bundle_ref
    ▼
OpenClaw Runtime Session             ← execution layer
```

### 5.1 Naming Clarification

The governance edge from `PersonaCapitalBinding` travels through the chain
under two field names:

- `DeploymentPlan.binding_id` — references `PersonaCapitalBinding.binding_id`
- `RuntimeBinding.persona_capital_binding_id` — same reference at runtime

Both refer to the same `PersonaCapitalBinding.binding_id`. Session audit
resolves this edge via:
```
SessionPersona.runtime_binding_id
    → RuntimeBinding.persona_capital_binding_id
    → PersonaCapitalBinding.binding_id
```

This naming drift is a known artifact of CAP-001/DEP-001/RUN-001 phased
delivery. BFF and lineage layers MUST use the resolution path above and
MUST NOT introduce a fourth name for this edge.

---

## 6. Service Boundary

### 6.1 Persona Plane owns

- `PersonaRegistry` — CRUD and lifecycle transitions for Persona objects
- `PersonaSessionStore` — create / terminate / quarantine sessions
- `CapabilitySnapshot` — compute and store capability snapshots (incl. `denial_reasons`)
- `PersonaPolicyResolver` — resolve `route_policy_id`, `consult_policy_id`, `tool_profile_id`
  from a policy catalog into an auditable `PolicyResolutionResult`; fail-closed on unknown IDs
- Session type enforcement (which sessions require runtime binding)
- Lifecycle state transition validation

### 6.2 Persona Plane does NOT own

- `PersonaCapitalBinding` — owned by Capital Pool / Governance Plane (CAP-001)
- `DeploymentPlan` — owned by Governance / Deployment Plane (DEP-001)
- `RuntimeBinding` — owned by Execution Plane / Runtime Manager (RUN-001)
- OpenClaw session execution — delegated to `openclaw-gateway-adapter`
- Tool / skill resolution beyond the snapshot reference — OpenClaw runtime
- Trainer teaching commit evaluation — owned by `training-session-svc`; persona
  policy-affecting commits must present a completed evaluation proof and passed
  governance gate state before Persona Plane or routing policy surfaces treat the
  candidate as committed

### 6.3 Resolution order (session creation)

1. Verify persona exists and `lifecycle_state` allows the session type
2. Check persona `status` — suspended personas are denied immediately (fail closed)
3. Run `PersonaPolicyResolver.resolve(persona, ...)` to get `PolicyResolutionResult`
   - `tool_profile_id` → `effective_tools` (fail closed if unknown)
   - `route_policy_id` → `effective_workflows` + route skills (fail closed if unknown)
   - `consult_policy_id` → consult skills (fail closed if unknown)
   - If `capital_pool_id` + `target_scope` provided → capital eligibility check
4. Build `CapabilitySnapshot` from the result (includes `source_refs` and `denial_reasons`)
5. For deployment-bound sessions: look up active `RuntimeBinding` for the pool
6. Populate `runtime_binding_id`, `deployment_stage`, `capital_pool_id`
7. Create `SessionPersona` record
8. Pass to `openclaw-gateway-adapter` for runtime session creation

**Fail-closed rule:** an unknown or disallowed policy ID causes the corresponding capability
bucket to be empty and a denial reason to be recorded in `CapabilitySnapshot.denial_reasons`.
The session is still created unless the persona is suspended or a capital eligibility failure
makes the requested scope invalid.

---

## 7. API Surface

### Persona Registry APIs
- `POST /api/personas` — create persona
- `GET /api/personas/{persona_id}` — get persona by ID
- `GET /api/personas` — list personas (filter by lifecycle_state, status)
- `PATCH /api/personas/{persona_id}/lifecycle` — advance lifecycle state

### Capability APIs
- `POST /api/personas/{persona_id}/capabilities/snapshot` — compute snapshot
- `GET /api/capabilities/snapshots/{snapshot_id}` — get snapshot by ID

### Session APIs
- `POST /api/personas/{persona_id}/sessions` — create session
- `GET /api/sessions/{session_id}` — get session by ID
- `GET /api/sessions` — list sessions (filter by persona_id, status, type)
- `POST /api/sessions/{session_id}/terminate` — terminate session
- `POST /api/sessions/{session_id}/degrade` — mark session degraded
- `POST /api/sessions/{session_id}/quarantine` — quarantine session

---

## 8. Audit Requirements

Every session MUST carry these fields for audit and replay:

| Field | Source | Required |
|---|---|---|
| `persona_id` | Persona registry | Always |
| `session_id` | Session store | Always |
| `session_type` | Session creation | Always |
| `trace_id` | Caller | Always |
| `request_id` | Caller | Always |
| `capability_snapshot_id` | Computed at session start | Always |
| `runtime_binding_id` | RuntimeBinding lookup | When deployment-bound |
| `deployment_stage` | RuntimeBinding.deployment_mode | When runtime_binding_id set |
| `capital_pool_id` | RuntimeBinding.capital_pool_id | When runtime_binding_id set |
| `context_bundle_ref` | Session context bundle | Recommended |

---

## 9. Acceptance Criteria (PER-001 + MPOS-P1-PER-001)

- [x] Persona identity, session, and runtime instance boundaries are explicit
  in platform contracts (this document + schemas + Python objects)
- [x] `SessionPersona.runtime_binding_id`, `deployment_stage`, `capital_pool_id`
  are first-class contract fields, not bundle convention
- [x] Naming clarification for `DeploymentPlan.binding_id` vs
  `RuntimeBinding.persona_capital_binding_id` is documented in §5.1
- [x] Lifecycle state machine is validated in Python with allowed transitions
- [x] JSON schemas cover Persona, SessionPersona, CapabilitySnapshot
- [x] `PersonaRegistry` and `PersonaSessionStore` enforce semantic rules
- [x] `PersonaPolicyResolver` resolves `route_policy_id`, `consult_policy_id`,
  `tool_profile_id` into effective capability decisions (MPOS-P1-PER-001)
- [x] Unknown or disallowed policy IDs fail closed with denial reasons recorded
  in `CapabilitySnapshot.denial_reasons`
- [x] Capital eligibility checked via binding store when `capital_pool_id` and
  `target_scope` are supplied; denial reason recorded on failure
- [x] Suspended persona fails completely closed before any policy resolution
- [x] Tests cover: allowed session, denied tool, denied capital scope, suspended persona

---

## 10. Non-Goals

- Implementation of `openclaw-gateway-adapter` (downstream of this contract)
- BFF read aggregation surfaces (APP-001)
- Operator-facing persona management UI (APP-002)
- Consultation session protocol details (CONSULTATION_SESSION_PROTOCOL.md)
- Full capability resolution spec (CAPABILITY_RESOLUTION_SPEC.md)

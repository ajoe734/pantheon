# Consultation Surface Contract

Last updated: 2026-04-10
Status: canonical — consultation surface contract for APP-001
Tier: L2 Planning & Execution (formal contract derived from L1 policy)
Scope: consultation surface definitions, canonical object citations, session types, degraded behavior, and operator journeys for consultation workflows
Owner: Qwen
Reviewer: Codex
Derived from: PERSONA_RUNTIME_MODEL.md, BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md, MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md, BINDING_AND_DEPLOYMENT_SEMANTICS.md

---

## 1. Purpose

This document defines the **consultation surfaces** for the governed BFF (APP-001).

A consultation surface enables personas to interact with each other in structured consultation sessions — requesting advice, responding as experts, or participating in committee decisions. **Every consultation surface cites canonical L1 objects and does not create shadow copies or parallel truth sources.**

It establishes:
- Consultation session types and their canonical object citations
- Consultation API routes and request/response shapes
- Consultation degraded behavior and fallback paths
- Consultation operator journeys

---

## 2. Consultation Model

### 2.1 Canonical Basis

The consultation model derives directly from **PERSONA_RUNTIME_MODEL.md**:

- **§6 Consult Policy Model**: Defines `required_reviewers`, `required_committees`, `trigger_rules`, `forbidden_solo_actions`, `escalation_rules`
- **§2.2 Session Persona**: Consultation is a `session_type` — specifically `consult` and `committee`
- **§13 Persona & Consultation Relationship**: Three consultation roles — `requester`, `responder`, `committee_participant` — all realized through Session Persona, not direct registry persona messaging
- **§7 Capability Snapshot**: Each consultation session has an immutable capability snapshot computed at session start

### 2.2 Consultation Session Projection

A consultation session surface is a **read projection of SessionPersona** where `session_type = "consult"` or `session_type = "committee"`.

The BFF may reshape the payload for presentation, but the canonical backing data still comes from:
- core `SessionPersona` fields defined in PERSONA_RUNTIME_MODEL.md §14
- `SessionPersona.metadata.consultation.*` values persisted by the Persona Plane at session creation / update time

The BFF must not invent consultation state that is absent from those upstream records.

```
SessionPersona (consultation projection)
├── session_id                  — unique session identifier
├── persona_id                  — the consulting persona
├── session_type                — "consult" | "committee"
├── status                      — "active" | "terminated" | "degraded" | "quarantined" | "audit_replay"
├── started_at                  — UTC ISO-8601
├── capability_snapshot_id      — frozen capability set at session start
├── trace_id                    — distributed trace identifier (required)
├── request_id                  — idempotency / correlation identifier (required)
├── context_bundle_ref          — reference to consultation context
├── task_ref                    — reference to the consultation task
├── runtime_binding_id          — nullable; active RuntimeBinding if consultation affects a live runtime
├── deployment_stage            — nullable; paper/canary/live/frozen (co-dependent with runtime_binding_id)
├── capital_pool_id             — nullable; co-dependent with runtime_binding_id
├── metadata.consultation.*     — consultation-specific fields materialized by Persona Plane (see §2.3)
├── ended_at                    — nullable; session end time
```

### 2.3 Consultation-Specific Metadata

```
metadata.consultation
├── consultation_type           — "pre_deployment" | "risk_review" | "macro_regime_shift" | "incident_response" | "policy_change" | "general"
├── requester_session_id        — SessionPersona ID of the requesting persona (for committee sessions)
├── responder_session_ids[]     — Array of responder SessionPersona IDs
├── committee_session_ids[]     — Array of committee participant SessionPersona IDs (for committee sessions)
├── consult_policy_ref          — reference to the ConsultPolicy that triggered this session
├── trigger_rule                — the specific rule that initiated the consultation
├── required_reviewers          — minimum responders needed for valid consultation
├── required_committees[]       — required committee identities, if any
├── forbidden_solo_actions[]    — policy actions that required this consultation
├── actual_reviewers            — count of responders who responded
├── outcome                     — "approved" | "rejected" | "conditional" | "escalated" | "abandoned"
├── rationale_ref               — optional reference to stored rationale content
├── evidence_refs[]             — references to supporting artifacts, telemetry, lineage, incidents
└── escalation_path             — if escalated, where it was escalated to
```

### 2.3 Consultation Roles

| Role | Description | Canonical Object |
|---|---|---|
| **Requester** | The persona that initiates the consultation | SessionPersona (`session_type: consult`) |
| **Responder** | A persona that provides consultation input | SessionPersona (`session_type: consult` or `committee`) |
| **Committee Participant** | A persona participating in a multi-persona consultation | SessionPersona (`session_type: committee`) |
| **Consultation Observer** | A persona with read-only access to consultation results | SessionPersona (read-only capability snapshot) |

---

## 3. Consultation API Routes

### 3.1 Consultation Session Management

All consultation surfaces are **read-oriented** on the BFF. Session creation is performed by the **Persona Plane** (canonical write authority); the BFF exposes these as read surfaces.

| Route | Method | Surface | Response | Description |
|---|---|---|---|---|
| `/api/v1/personas/{persona_id}/consultations` | GET | CS-01 | `{ data: [SessionPersona consultation projection], meta }` | List consultations for a persona |
| `/api/v1/consultations/{session_id}` | GET | CS-02 | `{ data: SessionPersona consultation projection, meta }` | Consultation session detail |
| `/api/v1/consultations/{session_id}/participants` | GET | CS-03 | `{ data: [SessionPersona], meta }` | All participants in a consultation |
| `/api/v1/consultations/{session_id}/outcome` | GET | CS-04 | `{ data: consultation outcome projection, meta }` | Session-backed consultation outcome view |
| `/api/v1/consultations/{session_id}/evidence` | GET | CS-05 | `{ data: consultation evidence ref list, meta }` | Evidence links attached to consultation |
| `/api/v1/personas/{persona_id}/consult-policy` | GET | CS-06 | `{ data: ConsultPolicy, meta }` | Consult policy for a persona |

### 3.2 CS-01: Consultation List

```
GET /api/v1/personas/{persona_id}/consultations
  ?page=1
  &page_size=20
  &filter.consultation_type=pre_deployment
  &filter.status=active
  &sort=started_at
  &sort_dir=desc
```

**Response**:
```json
{
  "data": [
    {
      "id": "cs-20260410-001",
      "type": "consult_session",
      "persona_id": "p-risk-analyst",
      "session_type": "consult",
      "status": "active",
      "started_at": "2026-04-10T10:00:00Z",
      "capability_snapshot_id": "csnap-042",
      "trace_id": "trace-abc123",
      "metadata": {
        "consultation": {
          "consultation_type": "pre_deployment",
          "required_reviewers": 1,
          "actual_reviewers": 1,
          "outcome": null
        }
      },
      "_links": {
        "self": "/api/v1/consultations/cs-20260410-001",
        "participants": "/api/v1/consultations/cs-20260410-001/participants",
        "outcome": "/api/v1/consultations/cs-20260410-001/outcome"
      }
    }
  ],
  "meta": {
    "total": 12,
    "page": 1,
    "page_size": 20,
    "staleness": null
  }
}
```

### 3.3 CS-02: Consultation Detail

```
GET /api/v1/consultations/{session_id}
```

**Response**: Full SessionPersona consultation projection with all fields per §2.2-§2.3.

### 3.4 CS-03: Consultation Participants

```
GET /api/v1/consultations/{session_id}/participants
```

**Response**:
```json
{
  "data": [
    {
      "id": "sp-requester-001",
      "type": "session_persona",
      "persona_id": "p-alpha-trader",
      "session_type": "consult",
      "consultation_role": "requester",
      "status": "active",
      "started_at": "2026-04-10T10:00:00Z",
      "capability_snapshot_id": "csnap-042",
      "_links": {
        "self": "/api/v1/sessions/sp-requester-001",
        "persona": "/api/v1/personas/p-alpha-trader"
      }
    },
    {
      "id": "sp-responder-001",
      "type": "session_persona",
      "persona_id": "p-risk-analyst",
      "session_type": "consult",
      "consultation_role": "responder",
      "status": "terminated",
      "started_at": "2026-04-10T10:00:30Z",
      "ended_at": "2026-04-10T10:02:00Z",
      "capability_snapshot_id": "csnap-078",
      "_links": {
        "self": "/api/v1/sessions/sp-responder-001",
        "persona": "/api/v1/personas/p-risk-analyst"
      }
    }
  ],
  "meta": {
    "total": 2,
    "staleness": null
  }
}
```

### 3.5 CS-04: Consultation Outcome

```
GET /api/v1/consultations/{session_id}/outcome
```

**Response**:
```json
{
  "data": {
    "session_id": "cs-20260410-001",
    "source_session": "/api/v1/consultations/cs-20260410-001",
    "metadata": {
      "consultation": {
        "outcome": "conditional",
        "actual_reviewers": 1,
        "responder_session_ids": ["sp-responder-001"],
        "rationale_ref": "workspace://consultation-rationales/cs-20260410-001",
        "evidence_refs": [
          {
            "type": "telemetry",
            "ref": "/api/v1/telemetry/artifact-042/performance?time_range=30d"
          },
          {
            "type": "lineage",
            "ref": "/api/v1/lineage?artifact_id=artifact-042"
          }
        ],
        "escalation_path": null
      }
    }
  },
  "meta": {
    "staleness": null
  }
}
```

### 3.6 CS-05: Consultation Evidence

```
GET /api/v1/consultations/{session_id}/evidence
```

**Response**:
```json
{
  "data": [
    {
      "id": "ev-001",
      "type": "evidence_link",
      "evidence_type": "telemetry",
      "artifact_ref": "artifact-042",
      "description": "30-day performance metrics",
      "link": "/api/v1/telemetry/artifact-042/performance?time_range=30d"
    },
    {
      "id": "ev-002",
      "type": "evidence_link",
      "evidence_type": "lineage",
      "artifact_ref": "artifact-042",
      "description": "Full lineage chain for artifact-042",
      "link": "/api/v1/lineage?artifact_id=artifact-042"
    },
    {
      "id": "ev-003",
      "type": "evidence_link",
      "evidence_type": "incident",
      "incident_ref": "inc-20260401-003",
      "description": "Related incident case",
      "link": "/api/v1/incidents/inc-20260401-003"
    }
  ],
  "meta": {
    "total": 3,
    "staleness": null
  }
}
```

### 3.7 CS-06: Consult Policy

```
GET /api/v1/personas/{persona_id}/consult-policy
```

**Response**:
```json
{
  "data": {
    "id": "cp-risk-analyst",
    "type": "consult_policy",
    "persona_id": "p-risk-analyst",
    "required_reviewers": 1,
    "required_committees": [],
    "trigger_rules": [
      {
        "condition": "pre_deployment_live",
        "description": "Must consult before any live deployment"
      },
      {
        "condition": "macro_regime_shift",
        "description": "Must consult when macro regime shift detected"
      }
    ],
    "forbidden_solo_actions": [
      "approve_live_deployment",
      "increase_capital_allocation_above_20pct"
    ],
    "escalation_rules": [
      {
        "trigger": "responder_rejects",
        "escalate_to": "governance_committee"
      }
    ]
  },
  "meta": {
    "staleness": null
  }
}
```

---

## 4. Consultation Canonical Object Citations

Every consultation surface cites these canonical objects — **no shadow copies**:

| Consultation Field | Canonical Source Object | L1 Document |
|---|---|---|
| `session_id`, `persona_id`, `session_type` | SessionPersona | PERSONA_RUNTIME_MODEL.md §14 |
| `capability_snapshot_id` | CapabilitySnapshot | PERSONA_RUNTIME_MODEL.md §14 |
| `trace_id`, `request_id` | SessionPersona (audit requirements) | PERSONA_RUNTIME_MODEL.md §16 |
| `runtime_binding_id`, `deployment_stage`, `capital_pool_id` | SessionPersona (co-dependent triplet) | PERSONA_RUNTIME_MODEL.md §14 |
| `metadata.consultation.consultation_type`, `trigger_rule`, `consult_policy_ref` | ConsultPolicy materialized into SessionPersona metadata by Persona Plane | PERSONA_RUNTIME_MODEL.md §6, §14 |
| `metadata.consultation.required_reviewers`, `required_committees`, `forbidden_solo_actions` | ConsultPolicy materialized into SessionPersona metadata by Persona Plane | PERSONA_RUNTIME_MODEL.md §6, §14 |
| `metadata.consultation.requester_session_id`, `responder_session_ids[]`, `committee_session_ids[]` | SessionPersona references stored in SessionPersona metadata | PERSONA_RUNTIME_MODEL.md §13, §14 |
| `metadata.consultation.outcome`, `actual_reviewers`, `rationale_ref`, `evidence_refs[]`, `escalation_path` | Session-scoped metadata persisted by Persona Plane; BFF may project but must not synthesize missing values | PERSONA_RUNTIME_MODEL.md §14 |
| `consultation_role` | Derived presentation label from requester / responder / committee session references | PERSONA_RUNTIME_MODEL.md §13, §14 |

---

## 5. Consultation Degraded Behavior

### 5.1 Degradation Matrix

| Surface | Primary Adapter | Fallback Strategy | Degradation Indicator |
|---|---|---|---|
| CS-01 (Consultation List) | Persona Adapter | Last-known sessions from cache | `"stale"` with `last_known_at` |
| CS-02 (Consultation Detail) | Persona Adapter | Cached session with staleness marker | `"stale"` or `"unavailable"` |
| CS-03 (Participants) | Persona Adapter | Reconstructed from trace_id references | `"partial"` with reconstruction note |
| CS-04 (Outcome) | Persona Adapter | Cached outcome if available | `"stale"` or `"unavailable"` |
| CS-05 (Evidence) | Telemetry/Lineage/Incident Adapters | Each evidence link validated independently; unavailable links marked | Per-link status in response |
| CS-06 (Consult Policy) | Persona Adapter | Cached policy with staleness marker | `"stale"` — policies change infrequently |

### 5.2 Consultation-Specific Degradation Rules

1. **Active consultations**: If the Persona Plane is unavailable during an active consultation, the BFF serves the last-known consultation state with a staleness indicator. It never shows "no active consultations" — that is a dangerous false negative during governance-critical moments.

2. **Evidence links**: Each evidence link in CS-05 is validated independently. If the Telemetry service is down but the Lineage service is up, the telemetry link shows `"unavailable"` while the lineage link returns fresh data.

3. **Consult policy**: Consult policies change infrequently. The BFF may serve cached consult policy data with a staleness marker even when the Persona Plane is temporarily unavailable.

---

## 6. Consultation Operator Journeys

### 6.1 Pre-Deployment Consultation

```
Operator → CS-06 (consult policy for persona)
         → CS-01 (active consultations for persona)
         → CS-02 (consultation detail)
         → CS-04 (consultation outcome)
         → CS-05 (evidence reviewed)
         → DP-02 (deployment plan detail)
```

**Purpose**: Before deploying, the operator verifies that required consultations have completed successfully, reviews outcomes and evidence, and confirms the deployment plan.

### 6.2 Incident Response Consultation

```
Operator → IN-02 (incident detail)
         → CS-01 (consultations triggered by incident)
         → CS-03 (consultation participants)
         → CS-04 (consultation outcome)
         → RT-03 (runtime status)
         → IN-05 (kill switch status)
```

**Purpose**: During an incident, the operator checks what consultations were triggered, who is involved, what outcomes were reached, and the current runtime/kill-switch state.

### 6.3 Policy Review Consultation

```
Operator → CS-06 (consult policy)
         → CS-01 (historical consultations)
         → CS-04 (outcomes and rationales)
         → EV-01 (evolution decisions triggered)
         → LN-01 (lineage of affected artifacts)
```

**Purpose**: After a consultation cycle, the operator reviews what decisions were made, what evolution actions were triggered, and the full lineage chain.

---

## 7. Consultation Design Rules

### 7.1 No Shadow Consultation Model

The BFF **must not** maintain its own consultation state machine or synthesize an outcome from raw participant traffic. All consultation data flows from:

1. **SessionPersona** objects (canonical session state from PERSONA_RUNTIME_MODEL.md)
2. **ConsultPolicy** objects (canonical consultation rules from PERSONA_RUNTIME_MODEL.md §6)
3. **SessionPersona.metadata.consultation** values written by Persona Plane
4. **Evidence references** that point to canonical objects (TelemetryEvent, LineageEdge, IncidentCase)

### 7.2 Consultation Is Read-Only on BFF

The BFF consultation surfaces are **GET-only**. Session creation, outcome recording, and evidence attachment are performed by the **Persona Plane** (canonical write authority). The BFF only reads and composes consultation data for display.

### 7.3 Consultation and Multi-Persona Aggregation

When consultation involves multiple personas (committee sessions), the BFF presents each responder's persisted session reference separately — it does **not** infer votes, merge rationales, or synthesize a new committee verdict. Multi-persona aggregation belongs to the MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md policy and is handled by the Persona Plane, not the BFF.

---

## 8. Surface Count Summary

| Surface ID | Name | Canonical Objects | Route |
|---|---|---|---|
| CS-01 | Consultation List | SessionPersona consultation projection[] | `GET /api/v1/personas/{persona_id}/consultations` |
| CS-02 | Consultation Detail | SessionPersona consultation projection | `GET /api/v1/consultations/{session_id}` |
| CS-03 | Consultation Participants | SessionPersona[] | `GET /api/v1/consultations/{session_id}/participants` |
| CS-04 | Consultation Outcome | Consultation outcome projection | `GET /api/v1/consultations/{session_id}/outcome` |
| CS-05 | Consultation Evidence | Consultation evidence ref list | `GET /api/v1/consultations/{session_id}/evidence` |
| CS-06 | Consult Policy | ConsultPolicy | `GET /api/v1/personas/{persona_id}/consult-policy` |

**Total consultation surfaces**: 6

---

## 9. Verification Checklist

| APP-001 Acceptance Criterion | Status | Evidence |
|---|---|---|
| Consultation surfaces cite canonical objects | ✅ | §4 Canonical Object Citations — every field traces to SessionPersona, ConsultPolicy, or canonical evidence objects (TelemetryEvent, LineageEdge, IncidentCase) from L1 documents |
| No shadow model | ✅ | §7.1 — BFF does not maintain consultation state machine or outcome model; all data flows from canonical sources |
| Read-only on BFF | ✅ | §7.2 — all consultation surfaces are GET-only; writes are Persona Plane responsibility |
| Degraded path documented | ✅ | §5 Degradation Matrix and Degradation Rules |

---

*End of Consultation Surface Contract*

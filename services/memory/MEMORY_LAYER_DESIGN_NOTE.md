# Pantheon Memory Layer Design Note

**Task:** BG-004
**Phase:** Blueprint Gap P2
**Owner:** Claude
**Reviewer:** Codex
**Status:** review_ready
**Date:** 2026-04-13

---

## 1. Purpose

This design note formalises the Memory & Knowledge Plane, one of the eight canonical planes defined in the Pantheon blueprint.

The goal is to close GAP-04 from `Pantheon_Blueprint_Gap_Review_v1.md`:

> "Memory & Knowledge Plane 目前偏 registry / lineage，persona memory 與 institutional memory 不夠完整"

This document defines:
- The two primary memory object types
- Read/write authority model
- Write-back triggers (which platform events cause memory to be written)
- Retrieval contract (how sessions query memory)
- A worked example: postmortem → institutional memory → later research reuse

---

## 2. Memory Object Types

The Pantheon blueprint requires four memory kinds: episodic, semantic, persona memory, and shared institutional memory. These are represented by two first-class objects in the Memory Plane.

### 2.1 PersonaMemory

**Schema:** `services/memory/persona_memory.schema.json`

A PersonaMemory entry is **private to one persona**. It accumulates knowledge that is specific to how that persona has behaved, what it has learned, and what preferences have been confirmed by operators.

Key fields:
| Field | Role |
|---|---|
| `persona_id` | Hard scope: all access checks start here |
| `memory_type` | `episodic / semantic / preference / strategy_lesson / risk_observation / consultation_outcome` |
| `content.summary` | Human-readable; used as retrieval surface |
| `content.structured_payload` | Machine-readable (type-specific) |
| `source_event_type` | Which platform event triggered the write |
| `source_event_id` | Traceable back to the originating event record |
| `write_authority` | Service that wrote the entry (whitelist enforced) |
| `relevance_scope` | `persona_private` or `persona_and_committee` |
| `superseded_by` | Lineage chain if a lesson was updated |

### 2.2 InstitutionalMemoryEntry

**Schema:** `services/memory/institutional_memory_entry.schema.json`

An InstitutionalMemoryEntry is **system-wide** (or scoped to a strategy family or instrument class). It captures lessons and patterns that should be available to all personas, research sessions, and committee sessions.

Key fields:
| Field | Role |
|---|---|
| `knowledge_type` | `incident_lesson / regime_pattern / policy_precedent / research_finding / evolution_rationale / cross_persona_observation` |
| `content.headline` | One-sentence retrieval surface |
| `content.body` | Full narrative |
| `scope` | `system_wide / strategy_family / instrument_class` |
| `scope_filter` | Specific value when scope is not system_wide |
| `contributing_persona_ids` | Traceable to the personas whose outcomes generated this knowledge |
| `write_authority` | Service whitelist |
| `reuse_count` | Incremented by retrieval facade; used for ranking |

---

## 3. Read/Write Authority Model

### 3.1 Write Authority

Only designated services may write to either memory store. No session (interactive or research) may write directly.

| Service | Can Write PersonaMemory | Can Write InstitutionalMemory |
|---|---|---|
| `persona-memory-svc` | Yes (all types) | No |
| `incident-svc` | Yes (`strategy_lesson`) | Yes (`incident_lesson`) |
| `evolution-svc` | Yes (`strategy_lesson`) | Yes (`evolution_rationale`) |
| `trainer-svc` | Yes (`episodic`, `semantic`) | No |
| `consultation-svc` | Yes (`consultation_outcome`) | Yes (`cross_persona_observation`) |
| `research-svc` | Yes (`strategy_lesson`) | Yes (`research_finding`) |
| `governance-svc` | No | Yes (`policy_precedent`, `regime_pattern`) |

**Why this restriction:** Memory entries constitute a knowledge truth source. Allowing direct session writes would create unaudited mutation paths. All writes must go through a service that owns the triggering lifecycle event.

### 3.2 Read Authority

| Reader | PersonaMemory (own persona) | PersonaMemory (other persona) | InstitutionalMemory |
|---|---|---|---|
| Persona session (own persona_id) | Yes | No | Yes (scope-filtered) |
| Research session (own persona_id) | Yes | No | Yes |
| Committee/consult session | Yes, if `relevance_scope=persona_and_committee` | No | Yes |
| Operator (via BFF) | Yes, read-only summary | No | Yes |
| Trainer session | Yes (own persona_id) | No | Yes |
| Audit/replay session | Yes (frozen snapshot) | No | Yes (frozen snapshot) |

---

## 4. Write-Back Pipeline

Write-back is the process by which platform lifecycle events cause new memory entries to be created. The pipeline is event-driven: each service that owns a lifecycle event publishes a memory write request to `persona-memory-svc` or the institutional index after the event reaches a terminal/published state.

### 4.1 Write-Back Triggers

| Platform Event | Event Field | Memory Write |
|---|---|---|
| `SessionPersona` reaches `terminated` (normal end) | `session_id`, `persona_id` | PersonaMemory `episodic` — session outcome summary |
| `Postmortem` reaches `published` status | `postmortem_id`, `incident_id`, `persona_id` (via binding) | PersonaMemory `strategy_lesson` + InstitutionalMemory `incident_lesson` |
| `EvolutionDecision` reaches `approved` | `evolution_decision_id`, affected `persona_id` | PersonaMemory `strategy_lesson` + InstitutionalMemory `evolution_rationale` |
| Consultation session closes | `session_id`, `persona_id` | PersonaMemory `consultation_outcome`; InstitutionalMemory `cross_persona_observation` (if committee scope) |
| Trainer epoch completes | epoch metadata | PersonaMemory `episodic` + `semantic` |
| Operator confirms preference | operator feedback event | PersonaMemory `preference` |
| Research task reaches `done` | `task_id`, `persona_id` | PersonaMemory `strategy_lesson` + InstitutionalMemory `research_finding` |

### 4.2 Write-Back Sequence (Postmortem Example)

```
incident-svc                persona-memory-svc          institutional-index
     |                            |                            |
     |-- postmortem.status=published ---|                      |
     |                            |                            |
     |-- WritePersonaMemory(      |                            |
     |     persona_id=...,        |                            |
     |     memory_type=strategy_lesson,                        |
     |     source_event_type=postmortem_published,             |
     |     source_event_id=postmortem_id,                      |
     |     content.summary="lesson text"                       |
     |   ) ---------------------->|                            |
     |                            |-- embed + index ---------->|
     |                            |<-- embedding_ref ----------|
     |                            |-- store PersonaMemory ---->|
     |                            |                            |
     |-- WriteInstitutionalEntry( |                            |
     |     knowledge_type=incident_lesson,                     |
     |     source_event_id=postmortem_id,                      |
     |     scope=system_wide or strategy_family,               |
     |     content.headline="concise lesson",                  |
     |     content.body="full text"                            |
     |   ) -------------------------------------------->      |
     |                            |            embed + store --|
```

---

## 5. Retrieval Contract

Sessions query memory through the **retrieval facade** — a single read-only API that abstracts over both stores. The facade enforces the read authority rules from §3.2 before returning results.

### 5.1 Query Interface

```
GET /memory/retrieve
  ?persona_id=<id>          # required; scopes PersonaMemory access
  &scope=persona|institutional|both   # default: both
  &query=<natural language or tag>
  &knowledge_type=<filter>  # optional; filters InstitutionalMemory knowledge_type
  &memory_type=<filter>     # optional; filters PersonaMemory memory_type
  &limit=<n>                # default: 10
  &session_id=<id>          # required for audit trail
```

Response: ranked list of `PersonaMemory` and/or `InstitutionalMemoryEntry` objects, with `relevance_score` and `source_event_id` for traceability.

Production hardening note (2026-04-30):
- `GET /api/memory/retrieve` is implemented for institutional memory entries and remains the retrieval-facade entry point for future persona-memory merge-in.
- The facade performs a governance AuthZ check before store access. If `PANTHEON_GOVERNANCE_AUTHZ_URL`, `PANTHEON_GOVERNANCE_API_URL`, or `PANTHEON_GOVERNANCE_SERVICE_URL` is not configured, retrieval fails closed with `governance_authz_unconfigured`.
- Authorized institutional hits increment `reuse_count` through the store, preserving the ranking feedback loop.

### 5.2 Retrieval Trigger Contexts

| Context | Who calls retrieval | Typical query |
|---|---|---|
| Persona session start | `persona-memory-svc` (automatic) | "recent lessons and preferences for this persona" |
| Research task kickoff | research session | "prior findings relevant to this strategy/instrument" |
| Consultation/committee session | consultation-svc | "cross-persona observations relevant to this decision" |
| Operator review (BFF) | BFF → retrieval facade | "what does this persona remember about X" |
| Trainer epoch | trainer-svc | "semantic and episodic memories for this persona to inform curriculum" |
| Evolution review | evolution-svc | "regime patterns and incident lessons relevant to this persona's evolution" |

---

## 6. Worked Example: Postmortem → Institutional Memory → Research Reuse

### Step 1 — Incident occurs
Persona `LP-001` is involved in a flash-crash drawdown during canary deployment. `IncidentCase INC-2026-042` is raised. `SessionPersona` is quarantined.

### Step 2 — Postmortem published
`incident-svc` publishes `Postmortem PM-2026-042` (status = `published`).

Root cause: "LP-001 held a momentum position through a regime break because its regime-detection signal lagged by 2 bars."

### Step 3 — Write-back fires (dual path)

**PersonaMemory write (LP-001):**
```json
{
  "memory_id": "pm-002-lesson-001",
  "persona_id": "LP-001",
  "memory_type": "strategy_lesson",
  "content": {
    "summary": "Momentum signal lags 2 bars at regime breaks; reduce position sizing during regime-transition windows.",
    "structured_payload": {
      "affected_signal": "momentum_crossover",
      "lag_bars": 2,
      "regime_context": "trending_to_mean_reversion"
    },
    "tags": ["regime_break", "momentum", "position_sizing"]
  },
  "source_event_type": "postmortem_published",
  "source_event_id": "PM-2026-042",
  "written_at": "2026-04-13T09:00:00Z",
  "write_authority": "incident-svc",
  "relevance_scope": "persona_and_committee"
}
```

**InstitutionalMemoryEntry write (system_wide):**
```json
{
  "entry_id": "inst-regime-001",
  "knowledge_type": "incident_lesson",
  "content": {
    "headline": "Momentum strategies must widen position-sizing buffers during regime-transition windows.",
    "body": "Postmortem PM-2026-042 identified a 2-bar lag in regime-detection signals causing a drawdown for LP-001. This pattern is likely to affect any momentum persona operating with similar crossover signals.",
    "tags": ["momentum", "regime_break", "position_sizing"]
  },
  "source_event_type": "postmortem_published",
  "source_event_id": "PM-2026-042",
  "contributing_persona_ids": ["LP-001"],
  "written_at": "2026-04-13T09:00:00Z",
  "write_authority": "incident-svc",
  "scope": "strategy_family",
  "scope_filter": "momentum"
}
```

### Step 4 — Later research task reuses institutional memory

Three months later, a new research task `BG-NNN` evaluates whether to promote persona `LP-003` (also momentum family) to canary.

Research session queries:
```
GET /memory/retrieve
  ?persona_id=LP-003
  &scope=institutional
  &knowledge_type=incident_lesson
  &query=momentum regime break position sizing
  &limit=5
  &session_id=research-BG-NNN-001
```

Retrieval facade returns `inst-regime-001` ranked first (high relevance score, scope_filter matches `momentum`).

Research session incorporates the lesson: LP-003's canary promotion checklist now includes a gate — "regime-transition lag test must pass before canary approval."

This closes the loop: **incident lesson → institutional memory → future research gate**.

---

## 7. Service Architecture

Three lightweight services constitute the Memory Layer:

```
┌─────────────────────────────────────────────┐
│             persona-memory-svc              │
│  - Accepts WritePersonaMemory requests       │
│  - Enforces write_authority whitelist        │
│  - Embeds content and stores PersonaMemory   │
│  - Exposes read path for retrieval facade    │
└────────────────────┬────────────────────────┘
                     │
┌────────────────────▼────────────────────────┐
│           institutional-memory-index        │
│  - Accepts WriteInstitutionalEntry requests  │
│  - System-wide and scope-filtered entries   │
│  - Exposes read path for retrieval facade   │
└────────────────────┬────────────────────────┘
                     │
┌────────────────────▼────────────────────────┐
│             retrieval-facade                │
│  - Single read-only query API               │
│  - Enforces read authority per §3.2         │
│  - Merges ranked results from both stores   │
│  - Increments reuse_count on matches        │
└─────────────────────────────────────────────┘
```

Storage:
- Primary store: **PostgreSQL** (structured fields, lineage, audit)
- Vector index: **pgvector** (embedding_ref, semantic retrieval)
- No separate in-memory buffer for memory entries (unlike telemetry); all writes are durable by design.

Retention:
- Institutional memory entries receive `expires_at` from `PANTHEON_MEMORY_RETENTION_DAYS` on create when the writer did not provide an explicit expiration. The default is 365 days.
- `PANTHEON_MEMORY_RETENTION_DAYS=indefinite` disables automatic expiry for entries without `expires_at`.
- Expired entries are archived with `archived_at` and `archived_reason` and remain durable for lineage and replay, but active list/retrieval excludes them.

---

## 8. Acceptance Evidence Checklist

| Criterion | Evidence |
|---|---|
| At least 2 memory object schemas | `persona_memory.schema.json`, `institutional_memory_entry.schema.json` |
| One write-back pipeline | §4.2 — postmortem write-back sequence diagram |
| One retrieval query path | §5.1 — retrieval facade GET contract |
| postmortem → institutional memory → research reuse example | §6 — full end-to-end worked example |

---

## 9. Open Items (Not Blocking P2 Acceptance)

1. **Embedding model choice** — pgvector works with any embedding model; model selection is deferred to the research-svc team.

Resolved by production-hardening slice:
- **Retention / TTL policy** — implemented as create-time `expires_at` assignment plus archive-on-read/archive command behavior in `InstitutionalMemoryStore`.
- **Cross-plane replay** — covered by focused replay tests that write memory, restart the store, retrieve through the facade, and verify active archival exclusion/reuse persistence.
- **Auth/RBAC integration** — retrieval facade now checks governance AuthZ and fails closed when governance authorization is unavailable.

---

*Closes GAP-04. Depends on PLAN-002 for canonical plane taxonomy.*

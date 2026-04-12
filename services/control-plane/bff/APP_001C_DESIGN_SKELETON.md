# APP-001C BFF Design Skeleton

Last updated: 2026-04-10
Status: draft — BFF design skeleton for APP-001
Tier: L2 Planning & Execution (input for APP-001)
Scope: BFF architectural skeleton, component boundaries, data flow, and integration points
Derived from: APP-001A BFF_SURFACE_INVENTORY.md + APP-001B OWNER_PACKET.md + BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md
Reviewer: Codex

---

## 1. Purpose

This document provides the **architectural skeleton** for the governed BFF (APP-001). It consolidates the surface inventory (APP-001A) and query contract critique (APP-001B) into a structural blueprint that the APP-001 owner can directly implement once upstream dependencies (PER-001, LIN-001) are resolved.

It answers:
- What are the BFF's architectural components?
- How does data flow from downstream services to the UI?
- How are degradation and resilience built into the structure?
- What are the integration points with upstream and downstream planes?

It does **not** define:
- Final API routes (tracked in QUERY_CONTRACT_OUTLINE.md)
- Open policy questions (tracked in OPEN_QUESTIONS.md)
- Deployment topology (deferred per BFF_HA §9)

---

## 2. Architectural Principles

The following principles come directly from canonical L1 policy:

1. **No parallel truth** (BFF_SURFACE_INVENTORY.md §1): Every field displayed by the BFF must trace back to a canonical L1 object. The BFF is a read-model composer, never a source of truth.

2. **Control-plane isolation** (BFF_HA §2.2): BFF failure must not affect active runtimes. The BFF is on the control/UI plane, not the execution safety path.

3. **Partial degradation** (BFF_HA §5.1): If a downstream service is unavailable, only the affected surface degrades. Other surfaces remain operational.

4. **Secondary control path** (BFF_HA §6): High-privilege operators must have a non-BFF path for kill-switch, rollback, pause, and health diagnostics.

5. **Stateless operation** (BFF_HA §3.1): The BFF must not store canonical state locally. Session, cache, and notification cursor state must be externalized.

---

## 3. Component Architecture

The BFF is decomposed into the following logical components:

### 3.1 API Gateway Layer

```
┌─────────────────────────────────────────────────┐
│                 API Gateway Layer                │
├─────────────────────────────────────────────────┤
│  - Request routing & versioning (/api/v1/...)    │
│  - Auth / RBAC facade (BFF_HA §4)               │
│  - Rate limiting & request validation            │
│  - Standard response envelope injection          │
│  - SSE / WebSocket termination (future)          │
└─────────────────────────────────────────────────┘
```

**Responsibilities**:
- Authenticate incoming requests and resolve operator persona
- Enforce RBAC per surface (matrix defined in open questions)
- Route to appropriate downstream service adapter
- Inject standard response envelope (data + meta + staleness)
- Handle API versioning via URL path

**Interfaces**:
- Incoming: HTTP REST (GET, with POST for composed-view subscriptions future)
- Outgoing: Internal service adapter calls (HTTP/gRPC per downstream)

---

### 3.2 Read-Model Composition Layer

```
┌─────────────────────────────────────────────────┐
│            Read-Model Composition Layer          │
├─────────────────────────────────────────────────┤
│  - Surface query executor per domain              │
│  - Composed view assembler (journey endpoints)   │
│  - Staleness tracker & metadata builder           │
│  - Field-level degradation annotator              │
│  - Point-in-time snapshot coordinator             │
└─────────────────────────────────────────────────┘
```

**Responsibilities**:
- Execute queries against downstream service adapters
- Compose multi-surface views for operator journeys (APP-001A §4)
- Track and annotate staleness for each field or sub-surface
- Ensure point-in-time consistency for composed views (`snapshot_at`)
- Apply degradation behavior per surface group (APP-001A §5.2)

**Composition patterns**:

| Pattern | Use Case | Example |
|---|---|---|
| **Single-surface** | Direct pass-through to one downstream service | PS-01 (Persona List) → Persona Plane |
| **Multi-surface parallel** | Fetch from independent domains, merge in response | Deployment review: DP-02 + CP-02 + RT-02 |
| **Chained composition** | Output of one query constrains next | RT-02 detail uses RT-01 binding ID to fetch DeploymentPlan |
| **Cached fallback** | Serve stale data with staleness marker | PS-01/PS-02 from read-replica when Persona Plane down |

---

### 3.3 Service Adapter Layer

```
┌─────────────────────────────────────────────────┐
│              Service Adapter Layer               │
├──────────────┬──────────────┬───────────────────┤
│  Persona     │  Capital     │  Governance       │
│  Adapter     │  Pool Adpt.  │  Adapter          │
├──────────────┼──────────────┼───────────────────┤
│  Execution   │  Telemetry   │  Lineage          │
│  (Runtime)   │  Adapter     │  Adapter          │
├──────────────┼──────────────┼───────────────────┤
│  Incident    │  Evolution   │  (Future:         │
│  Adapter     │  Adapter     │   Feedback,       │
│              │              │   Registry, etc.) │
└──────────────┴──────────────┴───────────────────┘
```

**Responsibilities**:
- Translate canonical BFF queries into downstream service protocols
- Handle downstream service failures and return degradation metadata
- Enforce query timeouts and circuit breaking per adapter
- Map downstream response shapes to canonical BFF response shapes

**Adapter contract** (all adapters must implement):
```
interface ServiceAdapter {
  // Execute a query against the downstream service.
  // Returns data + metadata OR a degradation response.
  query(request: QueryRequest): AdapterResponse | DegradedResponse;

  // Health check for this downstream dependency.
  health(): AdapterHealth;
}

interface AdapterResponse {
  data: unknown;
  meta: {
    source_service: string;
    fetched_at: string;      // RFC 3339
    staleness: null;
  };
}

interface DegradedResponse {
  data: unknown | null;      // May include partial/stale data
  meta: {
    source_service: string;
    fetched_at: string;
    staleness: {
      served_from: "cache" | "read-replica" | "reconstructed";
      last_known_at: string;
    };
  };
  error?: {
    code: string;
    message: string;
  };
}

interface AdapterHealth {
  status: "healthy" | "degraded" | "unavailable";
  last_check: string;        // RFC 3339
}
```

---

### 3.4 External State Layer

```
┌─────────────────────────────────────────────────┐
│              External State Layer                │
├─────────────────────────────────────────────────┤
│  - Session store (externalized, per BFF_HA §3.4) │
│  - Cache layer (TTL TBD, per BFF_HA §9)         │
│  - Notification cursor store (externalized)      │
└─────────────────────────────────────────────────┘
```

**Responsibilities**:
- Store operator session state (auth tokens, persona context, preferences)
- Cache read-surface responses for degradation fallback
- Store notification cursors for SSE/stream reconnection

**Note**: Implementation mechanics (Redis, Memcached, etc.) are deferred per BFF_HA §9. The interface is defined here; the backing technology is a deployment decision.

---

### 3.5 Component Data Flow

```
Operator Request
       │
       ▼
┌──────────────────┐
│  API Gateway      │  Auth, RBAC, routing, validation
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Read-Model       │  Compose surfaces, track staleness,
│  Composition      │  assemble response
└────────┬─────────┘
         │
    ┌────┴────┬────────────┬────────┐
    ▼         ▼            ▼        ▼
┌────────┐┌────────┐┌────────┐┌────────┐
│Persona ││Capital ││Runtime ││Telemetry│ ... (per domain)
│Adapter ││Adapter ││Adapter ││Adapter  │
└───┬────┘└───┬────┘└───┬────┘└───┬────┘
    │         │          │         │
    ▼         ▼          ▼         ▼
  Persona  Capital   Execution  Telemetry
  Plane    Pool      Plane      Plane
  (downstream services)
```

**Response flow** (reverse):
1. Service adapters query downstream services
2. Adapters return `AdapterResponse` or `DegradedResponse`
3. Composition layer merges responses, adds staleness metadata
4. API gateway wraps in standard envelope and returns to operator

---

## 4. Surface-to-Component Mapping

Each surface from APP-001A §3 maps to exactly one service adapter:

| Domain | Surfaces | Adapter | Canonical Source |
|---|---|---|---|
| Persona (PS-01–PS-06) | Persona, SessionPersona, CapabilitySnapshot, TeachingSession | Persona Adapter | PERSONA_RUNTIME_MODEL.md |
| Capital Pool (CP-01–CP-04) | CapitalPool, PersonaCapitalBinding | Capital Pool Adapter | BINDING_AND_DEPLOYMENT_SEMANTICS.md |
| Deployment (DP-01–DP-04) | DeploymentPlan, ApprovalDecision | Governance Adapter | BINDING_AND_DEPLOYMENT_SEMANTICS.md |
| Runtime (RT-01–RT-04) | RuntimeBinding, RollbackRecord | Execution (Runtime) Adapter | BINDING_AND_DEPLOYMENT_SEMANTICS.md, ROLLBACK_AND_POSITION_SEMANTICS.md |
| Telemetry (TL-01–TL-03) | TelemetryEvent | Telemetry Adapter | TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md |
| Lineage (LN-01–LN-03) | LineageEdge | Lineage Adapter | LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md |
| Incident (IN-01–IN-05) | IncidentCase, PostmortemReport, FreezeOrder | Incident Adapter | TARGET_ARCHITECTURE.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md |
| Evolution (EV-01–EV-04) | EvolutionDecision, FreezeOrder, RollbackRecord | Evolution Adapter | EVOLUTION_REVIEW_AND_THRESHOLDS.md, ROLLBACK_AND_POSITION_SEMANTICS.md |

**Future surfaces** (APP-001A Appendix A) will add:
- Feedback Adapter (FB-01–FB-04)
- Registry Adapter (RG-01–RG-03)
- Research Adapter (RS-01–RS-03)

---

## 5. Degradation Architecture

### 5.1 Degradation Decision Tree

For each surface query, the BFF evaluates:

```
Is downstream service healthy?
├── Yes → Return fresh data
└── No → Is cached/replica data available?
         ├── Yes → Return stale data with staleness metadata
         └── No → Can partial data be reconstructed?
                  ├── Yes → Return reconstructed data with "partial" marker
                  └── No → Return "data unavailable" with last-known timestamp
```

### 5.2 Surface Group Degradation Matrix

From APP-001A §5.2, refined with component architecture:

| Surface Group | Primary Adapter | Fallback Strategy | Degradation Marker |
|---|---|---|---|
| IN-01–IN-05, RT-01–RT-04, CP-03–CP-04 | Incident, Execution, Capital Pool | Last-known state from cache | `"unverifiable"` |
| DP-01–DP-04, EV-01–EV-04, LN-01–LN-03 | Governance, Evolution, Lineage | Read-replica → reconstructed → unavailable | `"stale"` / `"partial"` / `"unverifiable"` |
| TL-01–TL-03, PS-01–PS-06, CP-01–CP-02 | Telemetry, Persona, Capital Pool | Read-replica with perf note | `"degraded"` |

### 5.3 Field-Level Staleness (Future)

Per APP-001B §2.7, the long-term target is field-level staleness:

```json
{
  "data": {
    "persona_id": "p-001",
    "name": "AlphaTrader",
    "lifecycle_state": {
      "value": "active",
      "staleness": null
    },
    "current_binding": {
      "value": "b-042",
      "staleness": {
        "served_from": "cache",
        "last_known_at": "2026-04-10T02:30:00Z"
      }
    }
  }
}
```

This is deferred to the formal API contract phase (APP-001 implementation).

---

## 6. Composed View Architecture

### 6.1 Journey-Based Composition

Each operator journey from APP-001A §4 becomes a **composed view**:

```
GET /api/v1/operator/deployment-review/{plan_id}
  → Composes: DP-02, CP-02, CP-04, RT-02, RT-04
  → Returns: { deployment_plan, capital_pool, bindings, runtime_binding, rollbacks, snapshot_at }

GET /api/v1/operator/incident-response/{incident_id}
  → Composes: IN-02, RT-03, TL-02, RT-04, EV-04, IN-05
  → Returns: { incident, runtime_binding, telemetry_summary, rollbacks, evolution_decisions, kill_switch, snapshot_at }

GET /api/v1/operator/post-incident-review/{incident_id}
  → Composes: IN-04, EV-01, EV-02, LN-01, TL-03
  → Returns: { postmortem, evolution_decisions, lineage_chain, performance_chart, snapshot_at }

GET /api/v1/operator/persona-management/{persona_id}
  → Composes: PS-02, CP-03, CP-04, PS-03, PS-05
  → Returns: { persona, bindings, sessions, teaching_history, snapshot_at }
```

### 6.2 Partial Rendering Contract

Each composed view includes a `meta.surfaces` metadata section:

```json
{
  "data": { ... composed data ... },
  "meta": {
    "snapshot_at": "2026-04-10T03:00:00Z",
    "surfaces": {
      "deployment_plan": { "status": "ok" },
      "capital_pool": { "status": "ok" },
      "runtime_binding": { "status": "degraded", "staleness": { ... } },
      "rollbacks": { "status": "unavailable" }
    }
  }
}
```

This allows the UI to render partial pages with explicit placeholders for unavailable surfaces.

---

## 7. Real-Time Feed Architecture (Deferred Design)

Per APP-001B §2.4, real-time feeds are required but not yet designed. The skeleton reserves the following subscription surfaces:

| Subscription | Purpose | Adapter |
|---|---|---|
| `SUB /api/v1/runtime/{runtime_id}/events` | Runtime state changes | Execution Adapter |
| `SUB /api/v1/incidents/stream` | Active incident feed | Incident Adapter |
| `SUB /api/v1/kill-switch/updates` | Kill-switch state changes | Incident Adapter |

Implementation mechanism (SSE vs WebSocket) is deferred to APP-001 formal design.

---

## 8. Security & RBAC Skeleton

Per APP-001B §2.5, an RBAC matrix is required but not yet defined. The skeleton reserves:

```
interface RBACPolicy {
  surface_id: string;
  required_role: string;
  scope: "global" | "capital_pool" | "persona";
  degraded_access: "allow" | "deny" | "readonly";
}
```

The RBAC facade component (BFF_HA §4) evaluates this policy before routing any request to the composition layer.

---

## 9. API Versioning Skeleton

Per APP-001B §2.6, URL-based versioning is the baseline:

```
/api/v1/...  → Current stable version
/api/v2/...  → Future version (when canonical objects evolve incompatibly)
```

Version transitions must align with canonical object evolution process (EVOLUTION_REVIEW_AND_THRESHOLDS.md).

---

## 10. Observability Requirements

Per BFF_HA §7, the BFF must emit:

| Metric | Purpose |
|---|---|
| `bff.request_rate` | Overall traffic volume |
| `bff.error_rate` | Error rate (4xx, 5xx) |
| `bff.downstream_error_rate` | Downstream dependency failures |
| `bff.render_latency_ms` | View-model composition time |
| `bff.sse_disconnect_rate` | Stream disconnect rate (future) |
| `bff.auth_error_rate` | Authentication/authorization failures |

These metrics feed into the telemetry and incident backbone (Phase 3 objects).

---

## 11. Secondary Control Path

Per BFF_HA §6, the following operations must have a non-BFF path:

| Operation | Secondary Path | RBAC |
|---|---|---|
| Kill-switch activation | Admin CLI / runtime-manager protected endpoint | Global operator + approver |
| Runtime rollback | Admin CLI / runtime-manager admin endpoint | Global operator |
| Runtime pause | Admin CLI / runtime-manager admin endpoint | Global operator |
| Health diagnostics | Control-plane internal API | Global operator |

The BFF design must document these paths but does not implement them.

---

## 12. Open Items Tracked Elsewhere

The following items are identified but tracked in companion documents:

| Item | Tracked In |
|---|---|
| Query parameter envelope (pagination, filtering, sorting) | QUERY_CONTRACT_OUTLINE.md |
| Standard response envelope shape | QUERY_CONTRACT_OUTLINE.md |
| RBAC matrix (surface → role mapping) | OPEN_QUESTIONS.md |
| Real-time feed mechanism (SSE vs WebSocket) | OPEN_QUESTIONS.md |
| Cache TTL and invalidation strategy | OPEN_QUESTIONS.md (deferred per BFF_HA §9) |
| Field-level staleness model | QUERY_CONTRACT_OUTLINE.md |
| API error response shapes | QUERY_CONTRACT_OUTLINE.md |
| Future surface integration sequencing | OPEN_QUESTIONS.md |

---

## 13. Verification Checklist for APP-001C Acceptance

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Design skeleton written | ✅ | This document |
| Query contract outline written | ✅ | QUERY_CONTRACT_OUTLINE.md |
| Open questions list written | ✅ | OPEN_QUESTIONS.md |

---

*End of APP-001C BFF Design Skeleton*

# APP-001B Owner Packet — Query Contract Critique & Read-Path Risk Inventory

Last updated: 2026-04-10
Status: draft — owner handoff packet for APP-001
Tier: L2 Planning & Execution (input for APP-001)
Scope: query contract critique, operator read-path risk inventory, and consolidated handoff for APP-001 owner
Derived from: APP-001A BFF_SURFACE_INVENTORY.md + L1 canonical policy documents
Reviewer: Codex

---

## 1. Purpose

This packet is the **APP-001B deliverable**. It provides the APP-001 owner with:

1. **Query Contract Critique** — gaps, ambiguities, and risks in the current BFF surface inventory (§2)
2. **Operator Read-Path Risk Inventory** — failure modes for each operator journey and surface group (§3)
3. **Consolidated Handoff Summary** — what the APP-001 owner can consume directly, and what needs follow-on work (§4)

This packet does **not** define new canonical objects, new BFF routes, or new policy. It critiques the existing inventory and identifies risks that APP-001 must address when designing the actual BFF contract.

---

## 2. Query Contract Critique

This section critiques the **BFF_SURFACE_INVENTORY.md** (APP-001A output) from the perspective of a future BFF query contract. It identifies gaps between the surface catalog and what a formal BFF API contract will need.

### 2.1 Missing Query Parameters & Filtering

**Issue**: The surface inventory defines `GET` patterns but does not specify filterable fields, pagination, or sorting for any surface.

| Surface Group | Missing Capability | Impact |
|---|---|---|
| PS-01 (Persona List) | No pagination, no `lifecycle_state` filter | Unbounded list for large persona registries |
| TL-01 (Telemetry Query) | `time_range` mentioned but no format (ISO 8601? RFC 3339?) | Ambiguous contract; consumers may send incompatible formats |
| TL-01 (Telemetry Query) | No pagination or max window constraint | Risk of unbounded query on Postgres partitioned tables |
| LN-03 (Lineage Graph) | `depth` parameter present but no max depth | Unbounded graph traversal risk |
| IN-01 (Incident List) | No `status` (active/resolved) filter | Operators cannot quickly filter to active incidents |
| DP-01 (Deployment Plan List) | No `stage` (paper/canary/live) filter | Cannot quickly view plans by deployment stage |

**Recommendation for APP-001**: Define a **standard query envelope** that all list surfaces share:
```
GET /api/<resource>?page=&page_size=&sort=&sort_dir=&filter.<field>=
```
with documented defaults and maximums per surface.

### 2.2 No Response Shape Contract

**Issue**: The inventory specifies query patterns but not response shapes. A BFF contract needs:
- Envelope format (e.g., `{ data: [...], meta: { total, page, page_size } }`)
- Error response shape (e.g., `{ error: { code, message, details } }`)
- Staleness/degradation metadata format (per §5 degraded-path requirements)

**Recommendation for APP-001**: Define a **standard BFF response envelope** that includes:
```json
{
  "data": [],
  "meta": {
    "total": 0,
    "page": 1,
    "page_size": 20,
    "staleness": null
  }
}
```
Where `staleness` is `null` for fresh data, or `{ served_from: "cache" | "read-replica" | "reconstructed", last_known_at: "..." }` for degraded responses.

### 2.3 Composed View Ambiguity

**Issue**: The operator journeys (§4 of the inventory) imply **composite views** (e.g., Pre-Deployment Review Journey spans DP-03 → DP-02 → CP-02 → CP-04 → RT-02 → RT-04). The inventory does not define whether the BFF should:
- (a) Expose individual surfaces only (client composes)
- (b) Provide pre-composed journey endpoints
- (c) Both

**Risk**: If (a), the client makes 6+ sequential calls per journey, compounding latency and partial-failure risk. If (b), the BFF becomes a journey-specific endpoint factory that is hard to maintain.

**Recommendation for APP-001**: Adopt a **hybrid model**:
- Individual surfaces remain the canonical contract (option a baseline)
- BFF may expose **composed view endpoints** for critical journeys (e.g., `GET /api/operator/deployment-review/{plan_id}`) that aggregate multiple surfaces server-side
- Composed endpoints document which underlying surfaces they compose, so degradation can be traced to specific downstream failures

### 2.4 Real-Time Feed Gap

**Issue**: BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §4 lists "realtime feed to UI" as a BFF responsibility. The surface inventory is entirely synchronous `GET` patterns. There is no SSE/WebSocket surface defined for:
- Runtime state changes
- Telemetry streaming
- Incident alerts
- Kill-switch state changes

**Recommendation for APP-001**: Define **real-time subscription surfaces** for at least:
- `SUB /api/runtime/{runtime_id}/events` (runtime state changes)
- `SUB /api/incidents/stream` (active incident feed)
- `SUB /api/kill-switch/updates` (kill-switch state changes)

### 2.5 Authorization & RBAC Not Referenced

**Issue**: BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §4 lists "auth / RBAC facade" as a BFF responsibility. The surface inventory has no mention of:
- Which surfaces require which roles
- Whether any surfaces are persona-scoped vs operator-scoped
- How RBAC decisions propagate to degraded responses

**Recommendation for APP-001**: Define an **RBAC matrix** mapping each Surface ID to required roles and scope (persona, pool, global).

### 2.6 Versioning & Compatibility

**Issue**: No API versioning strategy is defined. As canonical objects evolve (e.g., new fields in `RuntimeBinding`, new `LineageEdge` types), the BFF contract needs a versioning strategy.

**Recommendation for APP-001**: Adopt **URL-based versioning** (`/api/v1/...`) at minimum, with documented deprecation policy aligned with the canonical object evolution process.

### 2.7 Degraded-Path Field-Level Granularity

**Issue**: The inventory's degraded-path assumptions (§5) operate at the **surface level** (entire surface unavailable). However, L1 policy documents define field-level semantics (e.g., `RuntimeBinding.deployment_mode` vs `RuntimeBinding.artifact.checksum`). A partial degradation may affect some fields but not others.

**Recommendation for APP-001**: Define a **field-level staleness model** where individual fields in a response can carry their own `staleness` indicator, not just the entire surface.

---

## 3. Operator Read-Path Risk Inventory

This section enumerates failure modes for each operator journey defined in BFF_SURFACE_INVENTORY.md §4, plus systemic risks that affect all journeys.

### 3.1 Pre-Deployment Review Journey Risk

```
Operator → DP-03 → DP-02 → CP-02 → CP-04 → RT-02 → RT-04
```

| Failure Point | Surface Affected | Degraded Behavior | Residual Risk |
|---|---|---|---|
| Governance Plane down | DP-01 to DP-04, CP-03 to CP-04 | "Approval state unverifiable" | **HIGH** — Operator cannot approve deployment without governance data. This journey is completely blocked. |
| Capital Pool Plane down | CP-01 to CP-04 | Stale binding state with staleness warning | **MEDIUM** — Operator can still review deployment plan and runtime binding, but cannot verify current budget/binding state. |
| Runtime Manager down | RT-01 to RT-04 | Last-known binding with staleness indicator | **MEDIUM** — Operator cannot verify current runtime state, but can review planned deployment. |
| Lineage service down (for RT-04 rollback chain) | LN-01 to LN-03 | Partial, reconstructed lineage | **LOW** — Rollback history can be reconstructed from direct object references. |

**Journey-level risk**: This journey has **6 surface dependencies** and **3 domain dependencies** (Governance, Capital Pool, Execution). A single domain failure degrades at least 2 surfaces in the journey. The BFF must support **partial journey rendering** — show available surfaces with explicit markers for unavailable ones.

### 3.2 Incident Response Journey Risk

```
Operator → IN-01 → IN-02 → RT-03 → TL-02 → RT-04 → EV-04 → IN-05
```

| Failure Point | Surface Affected | Degraded Behavior | Residual Risk |
|---|---|---|---|
| Incident Plane down | IN-01 to IN-04 | "Incident data unavailable" | **CRITICAL** — Operator cannot see incident details. Must fall back to secondary control path (CLI / admin API). |
| Runtime Manager down | RT-03, RT-04 | Last-known state with staleness | **HIGH** — Cannot verify current runtime state or recent rollbacks during active incident. |
| Telemetry Plane down | TL-01 to TL-03 | "Telemetry data unavailable" | **HIGH** — Cannot assess current PnL/drawdown during incident response. |
| Evolution Plane down | EV-01 to EV-04 | "Evolution data unverifiable" | **MEDIUM** — Cannot see recent rollback records or evolution decisions. |
| Kill-switch status unavailable | IN-05 | "Status unknown" with last-check timestamp | **CRITICAL** — Operator cannot verify kill-switch state. This is the most dangerous single-point failure in this journey. |

**Journey-level risk**: This journey is **safety-critical**. It depends on 4 domains and 7 surfaces. The L1 policy explicitly states BFF must **never be the sole path for kill-switch execution**. This journey must have a documented **secondary control path** that bypasses BFF entirely.

### 3.3 Post-Incident Review Journey Risk

```
Operator → IN-03 → IN-04 → EV-01 → EV-02 → LN-01 → TL-03
```

| Failure Point | Surface Affected | Degraded Behavior | Residual Risk |
|---|---|---|---|
| Incident Plane down | IN-03, IN-04 | "Data unavailable" | **MEDIUM** — Post-incident review can be deferred. Not time-critical. |
| Evolution Plane down | EV-01, EV-02 | "Evolution data unverifiable" | **LOW** — Review can proceed with partial data. |
| Lineage service down | LN-01 | Partial, reconstructed lineage | **LOW** — Partial lineage sufficient for initial review. |
| Telemetry analytical mirror down | TL-03 | Aggregated data from Postgres with performance note | **LOW** — Performance chart still available, just slower. |

**Journey-level risk**: This journey is **non-time-critical**. All surfaces can be degraded without blocking the core post-incident review activity. Lowest risk of all defined journeys.

### 3.4 Persona Management Journey Risk

```
Operator → PS-01 → PS-02 → CP-03 → CP-04 → PS-03 → PS-05
```

| Failure Point | Surface Affected | Degraded Behavior | Residual Risk |
|---|---|---|---|
| Persona Plane down | PS-01 to PS-06 | Cached persona metadata (PS-01/PS-02 only); sessions/teaching unavailable | **MEDIUM** — Persona detail can be served from cache, but session and teaching data unavailable. |
| Capital Pool Plane down | CP-03, CP-04 | Last-known binding state with staleness warning | **MEDIUM** — Cannot verify current binding state. |
| Governance Plane down (for binding approval state) | CP-03, CP-04 | "Binding state unverifiable" | **HIGH** — Cannot verify whether bindings are approved or pending. |

**Journey-level risk**: This journey depends on 2 domains (Persona, Capital Pool). Persona Plane has a defined cache fallback for metadata surfaces, which mitigates risk for PS-01/PS-02. Session and teaching surfaces have no cache fallback.

### 3.5 Systemic Risks Across All Journeys

| Risk | Affected Journeys | Mitigation |
|---|---|---|
| **BFF total outage** | All | Secondary control path (CLI / admin API). Per L1 policy, BFF failure must not affect active runtimes. |
| **Shared backing store failure** (cache, session, notification cursor) | All | Per L1 §3.4, shared state must be externalized. If external store fails, BFF degrades to stateless mode with no session persistence. |
| **Load balancer failure** (multi-replica BFF) | All | Per L1 §3.3, LB is required. LB failure = total BFF outage. Must have LB failover or direct replica access. |
| **Downwise cascade** (multiple domains degraded simultaneously) | All journeys with 3+ domain dependencies | Incident Response Journey (4 domains) is most vulnerable. Pre-Deployment Review Journey (3 domains) is second. |
| **Stale data served as current** | All journeys | Strict staleness metadata required. Never show "none" on failure. Always show "unavailable" or "unverifiable" with last-known timestamp. |
| **Race condition: governance state changes during operator review** | Pre-Deployment Review, Incident Response | BFF should include a `snapshot_at` timestamp in composed views so the operator sees a consistent point-in-time snapshot. |

---

## 4. Consolidated Handoff for APP-001 Owner

### 4.1 What You Can Consume Directly

From **APP-001A** (BFF_SURFACE_INVENTORY.md):
- **33 L1 canonical surfaces** across 8 domains — these are your baseline API routes
- **11 future/task-level surfaces** in Appendix A — plan for these but do not implement until their underlying objects enter L1 canonical scope
- **4 operator journeys** — these are your composite view specifications
- **Degraded-path principles** (§5 of the inventory) — these are your resilience requirements, derived from L1 policy

From **this packet** (APP-001B):
- **7 contract gaps** (§2.1–2.7) that your BFF design must address before the contract can be considered complete
- **4 journey-level risk assessments** (§3.1–3.4) plus **1 systemic risk section** (§3.5) with residual risk ratings and platform-wide mitigations
- **6 systemic risks** that affect all journeys and require platform-level mitigation

### 4.2 What You Must Add

The APP-001 owner should produce the following artifacts (not in APP-001B scope):

1. **BFF API Contract** — OpenAPI/JSON Schema spec for all 33 canonical surfaces, including:
   - Request/response shapes (§2.2)
   - Query parameter envelope (§2.1)
   - Versioning strategy (§2.6)
   - RBAC matrix (§2.5)

2. **Composed View Design** — Server-side aggregation endpoints for critical journeys (§2.3), with:
   - Per-surface degradation markers
   - Point-in-time snapshot consistency (`snapshot_at`)
   - Field-level staleness model (§2.7)

3. **Real-Time Feed Design** — SSE/WebSocket surfaces for runtime, incident, and kill-switch events (§2.4)

4. **Secondary Control Path Specification** — CLI / admin API fallback for:
   - Kill-switch execution (BFF must never be sole path)
   - Incident response when BFF is fully unavailable
   - Pre-deployment approval when governance plane is unreachable

5. **Cache & Staleness Strategy** — TTL, cache invalidation, and stale-serving mechanics (currently deferred as non-canonical implementation note in the inventory)

### 4.3 Priority Ordering

Based on the risk assessments above, the APP-001 owner should prioritize:

1. **Kill-switch secondary control path** — CRITICAL risk, safety-critical
2. **Response shape contract** — Foundation for all other contract work
3. **RBAC matrix** — Required before any surface can be implemented
4. **Real-time feed for runtime/incident/kill-switch** — Safety-critical
5. **Query parameter envelope** — Required for surfaces to be production-ready
6. **Composed view endpoints** — Performance and reliability improvement
7. **Field-level staleness model** — Degraded-path refinement
8. **Cache & staleness strategy** — Deferred but needed before production deployment

### 4.4 Dependency Status

APP-001 depends on:
- `PER-001` (persona registry/session/runtime model in platform contracts) — **todo**
- `LIN-001` (normalize lineage edges and define read-model aggregation contract) — **todo**

APP-001B (this packet) is designed to **parallel-enable** APP-001 by providing critique and risk analysis that does not depend on PER-001 or LIN-001 being complete. The APP-001 owner can begin contract design using this packet and the surface inventory while waiting for upstream dependencies.

---

## 5. Verification Checklist for APP-001B Acceptance

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Owner packet written | ✅ | This document |
| Query contract critique written | ✅ | §2 — 7 gaps identified with recommendations |
| Operator read-path risks enumerated | ✅ | §3 — 4 journey-level + 6 systemic risks assessed |

---

*End of APP-001B Owner Packet*

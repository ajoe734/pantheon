# APP-001C Open Questions List

Last updated: 2026-04-10
Status: draft — open questions for APP-001
Tier: L2 Planning & Execution (input for APP-001)
Scope: Unresolved design decisions, policy gaps, dependency risks, and sequencing questions for the BFF formal design
Derived from: APP-001A BFF_SURFACE_INVENTORY.md + APP-001B OWNER_PACKET.md + APP_001C_DESIGN_SKELETON.md + APP_001C_QUERY_CONTRACT_OUTLINE.md
Reviewer: Codex

---

## 1. Purpose

This document enumerates the **open questions** that the APP-001 owner must resolve during formal BFF design and implementation. Each question includes context, options, recommendation, and the stakeholder who should decide.

Questions are categorized by urgency and dependency status.

---

## 2. Critical Path Questions (Must Resolve Before APP-001 Implementation)

### Q1: RBAC Matrix — Surface-to-Role Mapping

**Context**: APP-001B §2.5 identified that no surface has an assigned role requirement. The BFF needs an RBAC matrix mapping each Surface ID to required roles and scope.

**Options**:
- **A. Derive from P4-001 router contract**: The control-plane routing contract (P4-001) already defines permission evaluation. Extend it to include BFF surface scopes.
- **B. Define BFF-specific RBAC**: Create a new RBAC model specific to BFF surfaces, with persona-scoped and pool-scoped roles.
- **C. Hybrid**: Use P4-001 roles as the base, add BFF-specific read-scopes as a thin extension layer.

**Recommendation**: **C (Hybrid)**. The router already defines `operator`, `approver`, `admin` roles. The BFF should reuse these as the base and add read-scope constraints (e.g., `operator:read:persona:{persona_id}`, `operator:read:pool:{pool_id}`).

**Decider**: APP-001 owner + Claude (P4-001 owner)
**Blocks**: All surface implementation
**Tracked for**: Formal API contract phase

---

### Q2: Real-Time Feed Transport — SSE vs WebSocket

**Context**: APP-001B §2.4 and QUERY_CONTRACT_OUTLINE.md §8 reserve real-time feed surfaces but do not specify the transport mechanism.

**Options**:
- **A. Server-Sent Events (SSE)**: Simpler, HTTP-native, automatic reconnection, unidirectional (server→client). Good for telemetry streaming, incident feeds, kill-switch updates.
- **B. WebSocket**: Bidirectional, lower latency, more complex connection management. Enables client-side subscription filtering.
- **C. Both**: SSE for simple feeds, WebSocket for interactive workbench surfaces.

**Recommendation**: **A (SSE)** for v1. The BFF's real-time feeds are server→client only (state changes, alerts, updates). SSE is simpler to implement, works through standard load balancers, and aligns with the BFF's stateless architecture (BFF_HA §3.1). WebSocket can be added later if bidirectional communication is needed for consultation surfaces.

**Decider**: APP-001 owner
**Blocks**: Real-time feed implementation (§8 of query contract)
**Tracked for**: Formal API contract phase

---

### Q3: Cache Strategy and TTL

**Context**: BFF_HA §9 explicitly defers cache strategy. However, the degradation model (QUERY_CONTRACT_OUTLINE.md §5) depends on having cached data to serve when downstream services are unavailable.

**Options**:
- **A. Per-surface TTL**: Each surface group defines its own TTL (e.g., persona metadata: 5 min, runtime binding: 30 sec, telemetry: 1 min).
- **B. Single global TTL**: One TTL for all cached surfaces (e.g., 2 minutes).
- **C. Event-driven invalidation**: Cache is invalidated when downstream services push update events (requires downstream service changes).

**Recommendation**: **A (Per-surface TTL)** for v1, with event-driven invalidation as a future enhancement. Safety-critical surfaces (runtime, kill-switch) need short TTLs; informational surfaces (persona metadata, teaching history) can tolerate longer TTLs.

**Decider**: APP-001 owner + Gemini (deployment topology)
**Blocks**: Degradation path implementation
**Tracked for**: Deployment topology phase (BFF_HA §9)

---

### Q4: PER-001 and LIN-001 Dependency Timing

**Context**: APP-001 depends on PER-001 (persona registry/session/runtime model) and LIN-001 (lineage read-model aggregation). Both are currently `todo`. APP-001C is designed to parallel-enable APP-001, but some questions cannot be resolved until upstream contracts are locked.

**Open sub-questions**:
- **Q4a**: Can the BFF persona surfaces (PS-01–PS-06) be designed against the APP-001A inventory alone, or must PER-001 contract be locked first?
- **Q4b**: Can the BFF lineage surfaces (LN-01–LN-03) be designed against the APP-001A inventory alone, or must LIN-001 contract be locked first?

**Recommendation**: The BFF design skeleton and query contract outline are sufficient for **interface design** (routing, request/response shapes, degradation behavior). The **field-level schema** for persona and lineage objects must wait for PER-001 and LIN-001 contracts. The APP-001 owner can proceed with everything except field-level schema until upstream contracts are locked.

**Decider**: APP-001 owner
**Blocks**: Field-level schema implementation (not structural design)
**Tracked for**: APP-001 implementation planning

---

## 3. Important Questions (Should Resolve Early, But Not Blocking)

### Q5: Composed View Consistency Model

**Context**: QUERY_CONTRACT_OUTLINE.md §6.3 defines a `snapshot=preferred` parameter for composed views. But what is the actual consistency guarantee?

**Options**:
- **A. Strong consistency**: All surfaces fetched within a bounded time window (e.g., 5 seconds). If any surface exceeds the window, the entire composed view fails.
- **B. Eventual consistency**: Each surface fetched independently; `snapshot_at` is the timestamp of the oldest surface. Client accepts potential inconsistency.
- **C. Hybrid**: Attempt strong consistency; fall back to eventual with degradation metadata if the window is exceeded.

**Recommendation**: **C (Hybrid)**. Strong consistency is ideal for safety-critical journeys (incident response, deployment review), but failing entirely when one surface is slow is worse than presenting slightly inconsistent data with a clear staleness marker.

**Decider**: APP-001 owner
**Blocks**: Composed view implementation
**Tracked for**: Formal API contract phase

---

### Q6: Error Localization for Composed Views

**Context**: When a composed view has 5 surface dependencies and 2 fail, how should the error be communicated?

**Options**:
- **A. Per-surface error in `meta.surfaces` metadata**: Include error details in the `meta.surfaces` section of the response.
- **B. Separate error array**: Add an `errors` array at the response root level.
- **C. HTTP 207 Multi-Status**: Use RFC 4918 multi-status response (complex, not widely supported in REST clients).

**Recommendation**: **A (Per-surface error in `meta.surfaces`)**. Keeps the error context tied to the specific surface that failed. Example:
```json
{
  "meta": {
    "surfaces": {
      "runtime_binding": {
        "status": "error",
        "error": { "code": "DOWNSTREAM_TIMEOUT", "message": "Runtime manager did not respond within 5s" }
      }
    }
  }
}
```

**Decider**: APP-001 owner
**Blocks**: Composed view error handling
**Tracked for**: Formal API contract phase

---

### Q7: Admin CLI / Secondary Control Path Specification

**Context**: BFF_HA §6 requires a secondary control path for kill-switch, rollback, pause, and health diagnostics. The BFF design skeleton (§11) documents the requirement but does not specify the implementation.

**Questions**:
- **Q7a**: Should the BFF team own the admin CLI spec, or is this a separate task?
- **Q7b**: Should the control-plane internal API be versioned alongside the BFF API?
- **Q7c**: What is the RBAC model for the secondary control path (stronger than BFF)?

**Recommendation**: This should be a **separate task** (potentially APP-002 operator surfaces) rather than part of APP-001. The BFF team should document the required operations and their RBAC requirements, but the CLI/internal API implementation is outside APP-001 scope.

**Decider**: APP-001 owner + Copilot (APP-002 owner)
**Blocks**: Secondary control path implementation (not APP-001 BFF)
**Tracked for**: APP-002 planning

---

### Q8: Future Surface Integration Sequencing

**Context**: APP-001A Appendix A defines 11 future surfaces (FB-01–FB-04, RG-01–RG-03, RS-01–RS-03, CP-05). When should these be integrated into the BFF?

**Recommended sequencing**:
1. **First**: Registry surfaces (RG-01–RG-03) — governance-critical, needed for deployment visibility
2. **Second**: Feedback surfaces (FB-01–FB-04) — needed for operator to see learning/training state
3. **Third**: Research surfaces (RS-01–RS-03) — informational, lower urgency
4. **Last**: PoolSleeve (CP-05) — depends on capital pool sleeve implementation

**Decider**: APP-001 owner
**Blocks**: Future surface implementation (not current APP-001 scope)
**Tracked for**: APP-001 post-v1 planning

---

## 4. Deferred Questions (Can Wait Until Later Phases)

### Q9: API Gateway Technology Choice

**Context**: The design skeleton (§3.1) defines an API Gateway Layer but does not specify the technology.

**Options**: FastAPI, Express.js, Go net/http, NGINX + upstream, etc.

**Status**: Deferred to implementation phase. Not a contract question.

---

### Q10: BFF Deployment Topology

**Context**: BFF_HA §9 defers deployment topology. This includes replica count, load balancer type, container orchestration, etc.

**Status**: Explicitly deferred per BFF_HA §9. Not an APP-001 design question.

---

### Q11: Observability Integration with Telemetry Backbone

**Context**: BFF_HA §7 requires the BFF to emit metrics. How do these integrate with the Phase 3 telemetry backbone (TEL-001, TL-01–TL-03)?

**Status**: Deferred until TEL-001 telemetry schema is locked. The BFF metrics listed in DESIGN_SKELETON.md §10 are sufficient for interface planning.

---

## 5. Question Summary Table

| ID | Question | Urgency | Decider | Status |
|---|---|---|---|---|
| Q1 | RBAC matrix — surface-to-role mapping | Critical | APP-001 + Claude | Open |
| Q2 | Real-time feed transport (SSE vs WebSocket) | Critical | APP-001 | Open |
| Q3 | Cache strategy and TTL | Critical | APP-001 + Gemini | Open (deferred per BFF_HA §9) |
| Q4 | PER-001 / LIN-001 dependency timing | Critical | APP-001 | Open |
| Q5 | Composed view consistency model | Important | APP-001 | Open |
| Q6 | Error localization for composed views | Important | APP-001 | Open |
| Q7 | Admin CLI / secondary control path spec | Important | APP-001 + Copilot | Open |
| Q8 | Future surface integration sequencing | Important | APP-001 | Open |
| Q9 | API gateway technology choice | Deferred | APP-001 | Deferred |
| Q10 | BFF deployment topology | Deferred | Gemini | Deferred |
| Q11 | Observability integration with telemetry | Deferred | APP-001 + Gemini | Deferred |

---

## 6. Verification Checklist for APP-001C Acceptance

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Design skeleton written | ✅ | APP_001C_DESIGN_SKELETON.md |
| Query contract outline written | ✅ | APP_001C_QUERY_CONTRACT_OUTLINE.md |
| Open questions list written | ✅ | This document |

---

*End of APP-001C Open Questions List*

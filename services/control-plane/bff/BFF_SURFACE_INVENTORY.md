# Governed BFF Surface Inventory

Last updated: 2026-04-10
Status: draft — BFF surface inventory for APP-001A
Tier: L2 Planning & Execution (input for APP-001)
Scope: canonical read surfaces, operator journeys, and degraded-path assumptions for all Phase 2–5 foundation objects, plus Appendix A follow-on surfaces
Conflict rule: this document cites canonical L1 policy objects only; it does not define new objects or shadow models

---

## 1. Purpose

This document inventories every **read surface** (query / list / detail view) that the governed BFF (APP-001) will need to expose to operators and persona-facing users.

It covers objects from:
- **Phase 2**: Capital Pool, PersonaCapitalBinding, DeploymentPlan, RuntimeBinding
- **Phase 3**: TelemetryEvent, LineageEdge, Incident Case, PostmortemReport
- **Phase 4**: EvolutionDecision, FreezeOrder, RollbackRecord
- **Phase 5 (foundation)**: Persona, SessionPersona, CapabilitySnapshot

Each surface references the canonical L1 document that defines the object's semantics.

> **Design rule**: BFF must never invent a parallel truth model. Every field displayed must trace back to a canonical L1 object or a derived read-model that is explicitly documented.

---

## 2. Canonical Object Catalog

The following table lists every canonical object that the BFF will need to read, along with its L1 source document and write authority.

**Only objects with canonical L1 policy sources are listed here.** Objects whose definitions live in task-level documents (FB-*, EV-*, LP-*, REG-*, RS-*, OC-*, etc.) are deferred to Appendix A.

| # | Canonical Object | L1 Source Document | Write Authority | BFF Role |
|---|---|---|---|---|
| 1 | `Persona` | PERSONA_RUNTIME_MODEL.md | Persona Plane | read |
| 2 | `SessionPersona` | PERSONA_RUNTIME_MODEL.md | Persona Plane | read |
| 3 | `CapabilitySnapshot` | PERSONA_RUNTIME_MODEL.md | Persona Plane | read |
| 4 | `TeachingSession` | PERSONA_RUNTIME_MODEL.md | Persona Plane | read |
| 5 | `CapitalPool` | BINDING_AND_DEPLOYMENT_SEMANTICS.md | Capital Pool Plane | read |
| 6 | `PersonaCapitalBinding` | BINDING_AND_DEPLOYMENT_SEMANTICS.md | Governance Plane → Capital Pool Plane | read |
| 7 | `ApprovalDecision` | BINDING_AND_DEPLOYMENT_SEMANTICS.md | Governance Plane | read |
| 8 | `DeploymentPlan` | BINDING_AND_DEPLOYMENT_SEMANTICS.md | Governance Plane | read |
| 9 | `RuntimeBinding` | BINDING_AND_DEPLOYMENT_SEMANTICS.md | Execution Plane (Runtime Manager) | read |
| 10 | `TelemetryEvent` | TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md | Telemetry Plane (Postgres canonical) | read |
| 11 | `LineageEdge` | LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md | Lineage Read Model | read |
| 12 | `IncidentCase` | TARGET_ARCHITECTURE.md (Incident backbone) | Incident Plane | read |
| 13 | `PostmortemReport` | TARGET_ARCHITECTURE.md (Incident backbone) | Incident Plane | read |
| 14 | `EvolutionDecision` | EVOLUTION_REVIEW_AND_THRESHOLDS.md | Evolution Controller + Review Owners | read |
| 15 | `FreezeOrder` | EVOLUTION_REVIEW_AND_THRESHOLDS.md + KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md | Evolution / Governance Plane | read |
| 16 | `RollbackRecord` | ROLLBACK_AND_POSITION_SEMANTICS.md | Runtime Manager (operational mitigation) | read |

---

## 3. Operator Read Surfaces by Domain

### 3.1 Persona Surfaces

**Source**: PERSONA_RUNTIME_MODEL.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| PS-01 | Persona List | `Persona` | `GET /api/personas` | List all personas with lifecycle_state, mandate, strategy_family |
| PS-02 | Persona Detail | `Persona`, `PersonaCapitalBinding[]` | `GET /api/personas/{persona_id}` | Full persona profile with active bindings |
| PS-03 | Persona Sessions | `SessionPersona[]` | `GET /api/personas/{persona_id}/sessions` | Active and historical sessions for a persona |
| PS-04 | Session Detail | `SessionPersona`, `CapabilitySnapshot` | `GET /api/sessions/{session_id}` | Session context, capabilities, and status |
| PS-05 | Teaching History | `TeachingSession[]` | `GET /api/personas/{persona_id}/teaching` | Teaching sessions and their control states |
| PS-06 | Capability View | `CapabilitySnapshot` | `GET /api/personas/{persona_id}/capabilities` | Effective tools/skills for a persona |

**Degraded path**: If Persona Plane is unavailable, BFF may serve cached persona metadata from a recent read-replica snapshot. Session and teaching data are not cached — show "data unavailable" with last-known timestamp.

---

### 3.2 Capital Pool & Binding Surfaces

**Source**: BINDING_AND_DEPLOYMENT_SEMANTICS.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| CP-01 | Capital Pool List | `CapitalPool` | `GET /api/capital-pools` | List all pools with status, risk policy ref |
| CP-02 | Pool Detail | `CapitalPool`, `PersonaCapitalBinding[]` | `GET /api/capital-pools/{pool_id}` | Pool detail with active bindings and current runtime binding |
| CP-03 | Binding List | `PersonaCapitalBinding[]` | `GET /api/bindings?capital_pool_id=...` | All bindings for a pool, showing role and allowed_deployment_scope |
| CP-04 | Binding Detail | `PersonaCapitalBinding`, `Persona` | `GET /api/bindings/{binding_id}` | Full binding with persona detail, mandate, budget, validity |

**Degraded path**: Binding data is governance-critical. If binding service is down, show last-known binding state with staleness warning. Never show "no bindings" when the service is down — that is a dangerous false negative.

---

### 3.3 Deployment Surfaces

**Source**: BINDING_AND_DEPLOYMENT_SEMANTICS.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| DP-01 | Deployment Plan List | `DeploymentPlan[]` | `GET /api/deployment-plans` | All deployment plans with status, stage, target pool |
| DP-02 | Plan Detail | `DeploymentPlan`, `ApprovalDecision` | `GET /api/deployment-plans/{plan_id}` | Full plan with pre/post checks, rollback target, schedule |
| DP-03 | Approval Decision List | `ApprovalDecision[]` | `GET /api/approval-decisions` | Decisions with outcome, reviewer, timestamp |
| DP-04 | Approval Detail | `ApprovalDecision` | `GET /api/approval-decisions/{decision_id}` | Decision rationale, evidence links, reviewer chain |

**Degraded path**: Deployment plan data can be served from a read-replica. Approval decisions are governance-critical — if unavailable, show "approval state unverifiable" rather than a stale decision.

---

### 3.4 Runtime Surfaces

**Source**: BINDING_AND_DEPLOYMENT_SEMANTICS.md, ROLLBACK_AND_POSITION_SEMANTICS.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| RT-01 | Runtime Binding List | `RuntimeBinding[]` | `GET /api/runtime-bindings` | Active runtime bindings with deployment_mode, artifact, version |
| RT-02 | Runtime Binding Detail | `RuntimeBinding`, `DeploymentPlan` | `GET /api/runtime-bindings/{binding_id}` | Full runtime binding with deployment plan reference, rollback chain |
| RT-03 | Runtime Status | `RuntimeBinding` | `GET /api/runtimes/{runtime_id}/status` | Current runtime state |
| RT-04 | Rollback History | `RollbackRecord[]` | `GET /api/runtimes/{runtime_id}/rollbacks` | Rollback records with action_type, target, position treatment |

**Degraded path**: Runtime binding state is operational truth. If runtime-manager is unreachable, show last-known binding with staleness indicator. Never infer "no active binding" from timeout.

---

### 3.5 Telemetry Surfaces

**Source**: TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| TL-01 | Telemetry Query | `TelemetryEvent[]` | `GET /api/telemetry?pool_id=&artifact_id=&time_range=` | Time-series telemetry from canonical Postgres store |
| TL-02 | Telemetry Summary | `TelemetryEvent[]` (aggregated) | `GET /api/telemetry/{runtime_id}/summary` | Aggregated telemetry summary |
| TL-03 | Performance Chart | `TelemetryEvent[]` (aggregated) | `GET /api/telemetry/{artifact_id}/performance` | PnL curve, drawdown chart, benchmark comparison |

**Degraded path**: Telemetry has two tiers — canonical (Postgres) and analytical (ClickHouse). If analytical mirror is down, BFF can serve aggregated data from Postgres with a performance degradation note. If canonical store is down, show "telemetry data unavailable" — never serve stale telemetry as current.

---

### 3.6 Lineage Surfaces

**Source**: LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| LN-01 | Lineage Chain | `LineageEdge[]` | `GET /api/lineage?artifact_id=...` | Full lineage chain from source to current runtime |
| LN-02 | Edge Detail | `LineageEdge` | `GET /api/lineage/edges/{edge_id}` | Single edge with from/to object types and IDs |
| LN-03 | Lineage Graph | `LineageEdge[]` (subgraph) | `GET /api/lineage/graph?root_type=&root_id=&depth=` | Subgraph traversal for deep lineage exploration |

**Degraded path**: Lineage read model is a derived read-model. If the lineage service is down, BFF may reconstruct partial lineage from direct object references (e.g., `artifact.run_id`, `deployment_plan.artifact_id`) — clearly marked as "partial, reconstructed lineage" not the authoritative read model.

---

### 3.7 Incident Surfaces

**Source**: TARGET_ARCHITECTURE.md (Incident backbone), KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| IN-01 | Incident List | `IncidentCase[]` | `GET /api/incidents` | Active and historical incidents with severity, status, affected pool |
| IN-02 | Incident Detail | `IncidentCase` | `GET /api/incidents/{incident_id}` | Full incident with evidence, mitigation actions |
| IN-03 | Postmortem List | `PostmortemReport[]` | `GET /api/postmortems` | Postmortem reports with findings, action items |
| IN-04 | Postmortem Detail | `PostmortemReport`, `IncidentCase` | `GET /api/postmortems/{report_id}` | Full postmortem with root cause analysis and remediation track |
| IN-05 | Kill Switch Status | `FreezeOrder`, `RuntimeBinding` | `GET /api/kill-switch/status` | Current kill switch state, affected runtimes, safe mode status |

**Degraded path**: Incident data is safety-critical. If incident service is down, show "incident data unavailable" — never show "no incidents" when the service is unreachable. Kill switch status must come from runtime-manager directly; if unavailable, show "status unknown" with last-check timestamp.

---

### 3.8 Evolution Surfaces

**Source**: EVOLUTION_REVIEW_AND_THRESHOLDS.md, ROLLBACK_AND_POSITION_SEMANTICS.md

| Surface ID | Name | Canonical Objects | Query Pattern | Description |
|---|---|---|---|---|
| EV-01 | Evolution Decision List | `EvolutionDecision[]` | `GET /api/evolution-decisions` | Decisions with action_type, risk_level, status, target object |
| EV-02 | Decision Detail | `EvolutionDecision` | `GET /api/evolution-decisions/{decision_id}` | Full decision with thresholds, reviewer chain, execution result |
| EV-03 | Freeze Orders | `FreezeOrder[]` | `GET /api/freeze-orders` | Active and historical freeze orders with scope and rationale |
| EV-04 | Rollback Records | `RollbackRecord[]` | `GET /api/rollbacks` | All rollback records across runtimes with action_type and outcome |

**Degraded path**: Evolution decisions are governance records. If evolution service is down, show "evolution data unverifiable" — do not present stale decisions as current governance state.

---

## 4. Operator Journeys

### 4.1 Pre-Deployment Review Journey

```
Operator → DP-03 (approval decisions) → DP-02 (deployment plan detail)
         → CP-02 (pool detail) → CP-04 (binding detail)
         → RT-02 (current runtime binding) → RT-04 (rollback history)
```

**Purpose**: Before approving a deployment, the operator reviews the deployment plan, approval decision, target pool's current bindings, and what rollback options exist.

### 4.2 Incident Response Journey

```
Operator → IN-01 (incident list) → IN-02 (incident detail)
         → RT-03 (runtime status) → TL-02 (telemetry summary)
         → RT-04 (rollback history) → EV-04 (rollback records)
         → IN-05 (kill switch status)
```

**Purpose**: During an incident, the operator needs to see the incident details, current runtime state, recent telemetry, what rollbacks have been attempted, and whether the kill switch is engaged.

### 4.3 Post-Incident Review Journey

```
Operator → IN-03 (postmortem list) → IN-04 (postmortem detail)
         → EV-01 (evolution decisions) → EV-02 (decision detail)
         → LN-01 (lineage chain) → TL-03 (performance chart)
```

**Purpose**: After an incident, the operator reviews the postmortem, any evolution decisions triggered, the full lineage chain of affected artifacts, and performance impact.

### 4.4 Persona Management Journey

```
Operator → PS-01 (persona list) → PS-02 (persona detail)
         → CP-03 (binding list) → CP-04 (binding detail)
         → PS-03 (session history) → PS-05 (teaching history)
```

**Purpose**: When managing a persona, the operator reviews the persona profile, its capital pool bindings, active sessions, and teaching history.

---

## 5. Degraded Path Assumptions

### 5.1 L1-Aligned Degradation Principles

The following principles come directly from **BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5** and **KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md**:

1. **Partial degradation** (§5.1): If a downstream service is unavailable, the BFF shows a degraded panel for that surface only. Other surfaces remain operational.

2. **Total outage** (§5.2): If BFF is fully unavailable, active runtimes, telemetry, and kill-switch must not be affected. Operators can use backup control paths (CLI / admin API).

3. **Consultation / workbench surfaces** (§5.3): These may degrade to async, delayed, read-only, or show "unavailable" — but must never affect the emergency control chain.

4. **Kill-switch independence** (v1 decisions §5): BFF must never be the sole path for kill-switch execution.

5. **No "none" on failure**: Never show "no data" or "none" when a downstream service is unreachable. Always show "data unavailable" or "unverifiable" with the last-known timestamp.

### 5.2 Degradation Behavior by Surface

| Surface Group | Degradation Behavior |
|---|---|
| IN-01 to IN-05, RT-01 to RT-04, CP-03 to CP-04 | Never show "none" when unavailable. Show "data unverifiable" with last-known timestamp. |
| DP-01 to DP-04, EV-01 to EV-04, LN-01 to LN-03 | Show stale data with staleness indicator if read-replica available; otherwise "unverifiable". |
| TL-01 to TL-03, PS-01 to PS-06, CP-01 to CP-02 | Serve from read-replica if primary is down. Show degradation note. |
| Future surfaces (Appendix A surfaces) | Degradation behavior to be defined when those objects enter L1 canonical scope. |

> **Implementation note (non-canonical)**: Cache TTL and stale-serving strategies will be defined in a follow-on task once BFF HA deployment topology is finalized. Per BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §9, cache strategy is explicitly deferred from the current L1 truth. The table above provides degradation *behavior* only — not implementation mechanics.

### 5.3 Partial Page Rendering

The BFF must support partial page rendering. If one surface in a composite view is unavailable, the rest of the page still renders with a placeholder for the missing surface.

---

## 6. Reuse-Ready Summary for APP-001 Owner

This inventory is structured for direct consumption by APP-001 (governed BFF and consultation surfaces). The APP-001 owner can:

1. **Use the surface catalog (§3) as a starting point for API routing design** — each Surface ID maps to a specific query pattern and set of canonical objects. The main body currently covers **33 L1 canonical surfaces across 8 domains** (PS-01–06, CP-01–04, DP-01–04, RT-01–04, TL-01–03, LN-01–03, IN-01–05, EV-01–04).

2. **Use the operator journeys (§4) as user flow specifications** — each journey traces a realistic operator task across multiple surfaces.

3. **Use the degraded path assumptions (§5) as BFF resilience requirements** — each tier specifies exactly how the BFF should behave when a downstream service is unavailable, using only L1-established principles.

4. **Reference the canonical object catalog (§2) for field-level schema design** — every object traces back to an L1 policy document that defines its exact semantics.

5. **No shadow model needed** — every surface references existing canonical objects. The BFF does not need to invent new domain objects or maintain its own truth.

6. **Additional surfaces for task-level objects** (registry entries, research packages, feedback events, learning artifacts, evaluator/critic results, optimization output, replication gate results, and derived objects from FB-*, EV-*, LP-*, REG-*, RS-*, OC-* tasks) are catalogued in Appendix A and should be incorporated when those objects enter L1 canonical scope.

---

## 7. Appendix: Surface-to-Object Mapping Matrix (L1 Objects Only)

| Surface | Objects Referenced | L1 Docs |
|---|---|---|
| PS-01 to PS-06 | Persona, SessionPersona, CapabilitySnapshot, TeachingSession | PERSONA_RUNTIME_MODEL.md |
| CP-01 to CP-04 | CapitalPool, PersonaCapitalBinding | BINDING_AND_DEPLOYMENT_SEMANTICS.md |
| DP-01 to DP-04 | DeploymentPlan, ApprovalDecision | BINDING_AND_DEPLOYMENT_SEMANTICS.md |
| RT-01 to RT-04 | RuntimeBinding, DeploymentPlan, RollbackRecord | BINDING_AND_DEPLOYMENT_SEMANTICS.md, ROLLBACK_AND_POSITION_SEMANTICS.md |
| TL-01 to TL-03 | TelemetryEvent | TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md |
| LN-01 to LN-03 | LineageEdge | LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md |
| IN-01 to IN-05 | IncidentCase, PostmortemReport, FreezeOrder, RuntimeBinding | TARGET_ARCHITECTURE.md, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md |
| EV-01 to EV-04 | EvolutionDecision, FreezeOrder, RollbackRecord | EVOLUTION_REVIEW_AND_THRESHOLDS.md, ROLLBACK_AND_POSITION_SEMANTICS.md |

---

## 8. Appendix A: Future Surfaces (Non-L1 / Task-Level Objects)

The following surfaces reference objects whose definitions live in task-level documents rather than canonical L1 policy documents. They are listed here for planning visibility but are **not** part of the current canonical inventory. These surfaces should be formalized when their underlying objects enter L1 canonical scope.

### A.1 Feedback & Learning Surfaces

**Source documents**: FB-001, FB-002, FB-003, LP-001–LP-005 (task-level, not L1 canonical)

| Surface ID | Name | Objects | Query Pattern | Description |
|---|---|---|---|---|
| FB-01 | Trader Feedback | `FeedbackEvent[]` | `GET /api/feedback?artifact_id=&persona_id=` | Trader approve/edit/reject events with rationale |
| FB-02 | Learning Artifacts | `LearningArtifact[]` | `GET /api/learning-artifacts` | DSPy bundles, imitation policies, preference models with lineage |
| FB-03 | Evaluation Results | `EvaluatorResult[]`, `CriticResult[]` | `GET /api/evaluations?artifact_id=` | Evaluation scores and critic findings |
| FB-04 | Optimization Output | `OptimizationOutput[]` | `GET /api/optimization-output` | Optimizer recommendations and registry handoff status |

**Objects referenced**: `FeedbackEvent`, `LearningArtifact`, `EvaluatorResult`, `CriticResult`, `OptimizationOutput`

**Degraded path**: Feedback and learning data are non-critical for operational safety. If these services are down, show "learning data temporarily unavailable" — this does not block operator decision-making.

### A.2 Registry Surfaces

**Source documents**: REG-001, REG-002, REG-003 (task-level, not L1 canonical)

| Surface ID | Name | Objects | Query Pattern | Description |
|---|---|---|---|---|
| RG-01 | Registry Entry List | `RegistryEntry[]` | `GET /api/registry` | All registered artifacts with state, version, lineage |
| RG-02 | Entry Detail | `RegistryEntry` | `GET /api/registry/{registry_id}` | Full entry with promotion history and lineage chain |
| RG-03 | Promotion History | `RegistryEntry[]` (filtered) | `GET /api/registry/{artifact_id}/promotions` | Promotion lifecycle from candidate to live |

**Objects referenced**: `RegistryEntry`, and associated promotion/lineage metadata.

**Degraded path**: Registry is governance-critical. If registry is down, show "registry state unverifiable" — never present stale artifact state as current.

### A.3 Research Surfaces

**Source documents**: RS-001, RS-002, RS-003 (task-level, not L1 canonical)

| Surface ID | Name | Objects | Query Pattern | Description |
|---|---|---|---|---|
| RS-01 | Research Package List | `ResearchPackage[]` | `GET /api/research-packages` | Discovered and ingested research materials |
| RS-02 | Package Detail | `ResearchPackage` | `GET /api/research-packages/{package_id}` | Research detail with normalization status and replication gate result |
| RS-03 | Replication Gate Results | `ReplicationGateResult[]` | `GET /api/replication-gates` | First-pass replication results before registry admission |
| RS-04 | OSS Activation-Ready Operations View | research orchestrator, policy-learning, research-worker gateway, OpenClaw adapter read models | `GET /api/v1/operator/research/oss-activation-ready` (`/oss-preactivation` alias) | Read-only capability, gate state, run history, artifact refs, logs, and error summary for activation-gated OSS backends; no production activation or write authority |

**Objects referenced**: `ResearchPackage`, `StrategySpec` (via OC-003), `ReplicationGateResult`

**Objects additionally referenced by RS-04**: dormant/activation-ready capability metadata plus run/job records, artifact refs, event logs, stdout/stderr excerpts, and error summaries from service-owned read APIs. RS-04 may show offline activation-ready gates, but it is still read-only and must not be used as evidence of Qlib/TRL/RL/W&B/OpenClaw production activation.

**Degraded path**: Research data is informational. If research plane is down, show "research data unavailable" — this does not affect operational safety.

### A.4 Future Capital Pool Objects

| Object | Reason Not Yet in Surface |
|---|---|
| `PoolSleeve` | Not yet implemented — future object per BINDING_AND_DEPLOYMENT_SEMANTICS.md §10. Surface would be `CP-05: GET /api/capital-pools/{pool_id}/sleeves`. |

### A.5 Surface Count Summary

| Domain | Surface IDs | Count |
|---|---|---|
| Persona (PS) | PS-01 to PS-06 | 6 |
| Capital Pool & Binding (CP) | CP-01 to CP-04 | 4 |
| Deployment (DP) | DP-01 to DP-04 | 4 |
| Runtime (RT) | RT-01 to RT-04 | 4 |
| Telemetry (TL) | TL-01 to TL-03 | 3 |
| Lineage (LN) | LN-01 to LN-03 | 3 |
| Incident (IN) | IN-01 to IN-05 | 5 |
| Evolution (EV) | EV-01 to EV-04 | 4 |
| **L1 Canonical Subtotal** | | **33** |
| Feedback & Learning (FB) | FB-01 to FB-04 | 4 |
| Registry (RG) | RG-01 to RG-03 | 3 |
| Research (RS) | RS-01 to RS-03 | 3 |
| Future Capital (CP-05) | PoolSleeve | 1 |
| **Non-L1 / Future Subtotal** | | **11** |
| **Grand Total** | | **44** |

> The main body of this document (§3) covers **33 L1 canonical surfaces**. Appendix A adds **11 future surfaces** for planning visibility, yielding a total of **44 surfaces across 12 domains** when all task-level objects are included.

---

## 9. Appendix B: Objects Not Yet in Scope

The following objects exist in the ecosystem but are **not** part of the BFF read surface for Phase 5:

| Object | Reason Not Included |
|---|---|
| `PoolSleeve` | Not yet implemented — future object (see Appendix A.4) |
| `AllocationPolicyArtifact` | Internal to optimizer-svc portfolio synthesis — not a direct BFF surface |
| `EventRecord` | Internal event bus record — not a user-facing surface |
| `OutboxRecord` | Internal messaging pattern — not user-facing |
| `MLflowRun` / `W&B Run` | Experiment tracking backend — covered via Learning Artifact surfaces (FB-02, future) |
| `SignalRecord` | Internal signal store — not a BFF surface |

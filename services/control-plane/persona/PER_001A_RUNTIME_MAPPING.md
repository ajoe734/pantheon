# PER-001A: Persona Runtime Mapping and Reviewer Packet

**Task:** PER-001A
**Owner:** Qwen
**Reviewer:** Codex
**Status:** Review-ready
**Phase:** Phase 4: Evolution Governance
**Depends on:** CAP-001, RUN-001A
**Tier:** L2 Support Slice (feeds PER-001 contract lock)

---

## 1. Purpose

This document maps the **three-layer persona model** (registry / session / runtime)
from `PERSONA_RUNTIME_MODEL.md` onto the **runtime binding** and **capital binding**
objects established by CAP-001 and RUN-001/RUN-001A.

It answers:
- which persona registry object connects to which capital binding
- which session persona maps to which runtime binding
- how subject resolution works across the full chain
- what fields must carry through for audit and lineage

This is a **prep document** — it does not change L1 policy. It prepares the field
inventory and resolution logic so PER-001 can lock the formal contract.

---

## 2. Source Documents Consumed

| Document | Task | Role in This Mapping |
|---|---|---|
| `PERSONA_RUNTIME_MODEL.md` | — | L1 source for registry/session/runtime three-layer model |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | — | L1 source for PersonaCapitalBinding, DeploymentPlan, RuntimeBinding semantics |
| `services/control-plane/governance/capital_pool.contract.md` | CAP-001 | CapitalPool + PersonaCapitalBinding governance contract |
| `services/control-plane/governance/persona_capital_binding.py` | CAP-001 | Python platform object — binding enums, store, validation |
| `services/control-plane/governance/persona_capital_binding.schema.json` | CAP-001 | Machine-readable binding schema |
| `services/control-plane/governance/deployment_plan.contract.md` | DEP-001 | DeploymentPlan field names, planner invariants, and rollback linkage |
| `services/control-plane/governance/deployment_plan.schema.json` | DEP-001 | Machine-readable DeploymentPlan schema |
| `services/execution/runtime-manager/contract.md` | RUN-001 | RuntimeManager write authority contract |
| `services/execution/runtime-manager/runtime_binding.py` | RUN-001 | Python platform object — RuntimeBinding fields and validation |
| `services/execution/runtime-manager/runtime_binding.schema.json` | RUN-001 | Machine-readable RuntimeBinding schema |
| `services/execution/runtime-manager/authority_matrix.md` | RUN-001A | Write authority matrix for Runtime Manager |
| `services/execution/runtime-manager/rollback_action_matrix.md` | RUN-001A | Rollback action execution matrix |

---

## 3. The Full Chain: Registry → Binding → Session → Runtime

### 3.1 Provenance Chain

```
Persona (Registry)
    │
    │ persona_id
    ▼
PersonaCapitalBinding                    ← governance admissibility
    │ binding_id
    │ persona_id
    │ capital_pool_id
    │ allowed_deployment_scope
    ▼
ApprovalDecision                         ← artifact approval
    │
    ▼
DeploymentPlan                           ← deployment intent
    │ plan_id
    │ binding_id                         ← current DEP-001 field name
    │ artifact_id
    │ target_stage
    ▼
RuntimeBinding                           ← actual running state
    │ binding_id
    │ plan_id
    │ persona_capital_binding_id
    │ runtime_id
    │ capital_pool_id
    │ artifact_id
    │ artifact_version
    │ deployment_mode (paper/canary/live/frozen)
    ▼
Session Persona → Runtime Persona        ← executing agent instance
    │ session_id
    │ persona_id
    │ capability_snapshot_id
    │ context_bundle_ref
    └── OpenClaw runtime session (agent / consult / workflow)
```

### 3.2 Key Invariant

> **The persona registry object never directly references a RuntimeBinding.**
> The chain always flows through `PersonaCapitalBinding → DeploymentPlan → RuntimeBinding`.
> A persona's deployable authority is expressed by binding, not by registry fields.

---

## 4. Field Mapping: Three-Layer Persona → Runtime + Capital Binding

### 4.1 Registry Persona → PersonaCapitalBinding

| Persona Registry Field | Maps To | PersonaCapitalBinding Field | Notes |
|---|---|---|---|
| `persona_id` | → | `persona_id` | Direct match; immutable key |
| `lifecycle_state` | → | `status` (indirect) | `draft` may only hold `pending` bindings; `research_only`/`consultable` may hold `active advisor` bindings; `paper_owner`/`live_owner` are required for the corresponding owner roles |
| `owner` | → | `created_by` | Governance operator who proposed the binding |
| `mandate` | → | `mandate` | Direct carry; binding mandate may be more specific |
| `strategy_family` | → | `metadata.strategy_family` | Optional metadata carry |
| `tool_profile_id` | ✗ | — | Not carried into binding; resolved at session time via capability snapshot |
| `route_policy_id` | ✗ | — | Not carried into binding; resolved at session time |
| `consult_policy_id` | ✗ | — | Not carried into binding; enforced at session creation |

### 4.2 PersonaCapitalBinding → DeploymentPlan

| PersonaCapitalBinding Field | Maps To | DeploymentPlan Field | Notes |
|---|---|---|---|
| `binding_id` | → | `binding_id` | Current DEP-001 schema uses `binding_id`; RuntimeBinding later carries the same governance edge as `persona_capital_binding_id` |
| `capital_pool_id` | → | `capital_pool_id` | Direct match |
| `role` | → | (implicit gate) | `advisor` cannot sponsor; `paper_owner` gates to paper; `live_owner` gates to all stages |
| `allowed_deployment_scope` | → | gate on `target_stage` | `binding.allowed_deployment_scope >= plan.target_stage` |
| `status = active` | → | (precondition) | Binding must be active for plan to proceed |
| `budget` | → | `scale.capital_scale_pct` (indirect) | Budget constrains deployment scale |

### 4.3 DeploymentPlan → RuntimeBinding

| DeploymentPlan Field | Maps To | RuntimeBinding Field | Notes |
|---|---|---|---|
| `plan_id` | → | `plan_id` | Direct reference (RUN-001 acceptance criteria) |
| `binding_id` | → | `persona_capital_binding_id` | Current DEP-001 uses `binding_id`; RUN-001 names the same logical edge `persona_capital_binding_id` on RuntimeBinding |
| `artifact_id` | → | `artifact_id` | Direct match |
| `artifact_version` | → | `artifact_version` | Direct match in `runtime_binding.py` / schema |
| `capital_pool_id` | → | `capital_pool_id` | Direct match |
| `target_stage` | → | `deployment_mode` | Must be equal (RUN-001 §5 stage enforcement) |
| `rollback.action_type` | → | `rollback_action_type` | Carried through on rollback (RUN-001A matrix) |
| — | → | `rollback_parent` | Points to previous binding_id on replace |

### 4.4 RuntimeBinding → Session Persona / Runtime Persona

| RuntimeBinding Field | Maps To | Session/Runtime Persona Field | Notes |
|---|---|---|---|
| `binding_id` | → | `runtime_binding_id` (proposed) | Current SessionPersona model has no explicit runtime-binding field; PER-001 should formalize this addition (§7.1) |
| `artifact_id` | → | `context_bundle_ref` | The referenced context bundle should resolve the active artifact context for replay/audit |
| `deployment_mode` | → | `context_bundle_ref` / session audit payload | Current SessionPersona model has no first-class `deployment_stage` field; carry it through the referenced context bundle until PER-001 formalizes it |
| `capital_pool_id` | → | `context_bundle_ref` / session audit payload | Pool context should travel with the session bundle for audit and operator views |
| `runtime_id` | → | OpenClaw runtime session ID | Runtime persona is bound to this runtime |
| `status = active` | → | session lifecycle | Session may only run while binding is active |

---

## 5. Subject Resolution Checklist

When any operation needs to determine "which persona is acting on which pool in which runtime,"
resolve in this order:

### Step 1: Identify the Persona Registry Object
- [ ] `persona_id` is known or resolvable from the request context
- [ ] Persona exists in registry and `lifecycle_state ≠ retired`
- [ ] Persona `lifecycle_state` permits the intended operation:
  - `research_only` → research sessions only, no deployment
  - `consultable` → consult sessions allowed
  - `paper_owner` → may sponsor paper deployments
  - `live_owner` → may sponsor paper/canary/live deployments

### Step 2: Resolve the PersonaCapitalBinding
- [ ] `capital_pool_id` is known from the request or deployment context
- [ ] Active `PersonaCapitalBinding` exists for `(persona_id, capital_pool_id)`
- [ ] Binding `status = active`
- [ ] Binding `role` authorizes the intended operation:
  - `advisor` → read-only recommendations, no deployment sponsorship
  - `paper_owner` → may sponsor paper-stage `DeploymentPlan`
  - `live_owner` → may sponsor any stage `DeploymentPlan`
- [ ] `allowed_deployment_scope >= intended target_stage`
- [ ] If `live_owner`: single-live-owner rule verified (no other active `live_owner` for this pool)

### Step 3: Resolve the DeploymentPlan
- [ ] `DeploymentPlan` exists with `status ∈ {approved, executing}`
- [ ] Plan references the correct governance binding (`binding_id` in current DEP-001 schema)
- [ ] Plan `target_stage` is within binding's `allowed_deployment_scope`
- [ ] If `CapitalPool.single_runtime_enforced = true`: no existing `active` RuntimeBinding for this pool (or previous binding is being retired as part of this operation)
- [ ] Artifact loader checks passed

### Step 4: Resolve the RuntimeBinding
- [ ] `RuntimeBinding` exists (or is being created by Runtime Manager)
- [ ] `RuntimeBinding.plan_id` matches the DeploymentPlan
- [ ] `RuntimeBinding.persona_capital_binding_id` matches the PersonaCapitalBinding
- [ ] `RuntimeBinding.deployment_mode` equals `DeploymentPlan.target_stage`
- [ ] `RuntimeBinding.status = active` (for active operations)
- [ ] Single-runtime rule satisfied for the pool

### Step 5: Resolve the Session Persona
- [ ] `SessionPersona` created with correct `persona_id`
- [ ] `SessionPersona.session_type` matches the operation (`interactive`, `trainer`, `research_task`, `consult`, `background_job`, etc.)
- [ ] `CapabilitySnapshot` computed from registry persona's route policy + consult policy + RBAC
- [ ] Session carries runtime-binding / stage / pool context either via explicit PER-001 fields or the referenced context bundle

### Step 6: Resolve the Runtime Persona
- [ ] Runtime persona bound to an OpenClaw-compatible runtime session
- [ ] Runtime session has access to the correct `artifact_id` (via RuntimeBinding context)
- [ ] Runtime session respects the `deployment_mode` (paper = simulated, canary = limited real, live = full)
- [ ] Runtime events emitted with `artifact_id`, `deployment_stage`, `plan_id` tags

---

## 6. Cross-Cutting Rules

### 6.1 Lifecycle State Compatibility Matrix

| Persona `lifecycle_state` | Binding `role` allowed | Binding `status` allowed | Runtime `deployment_mode` allowed |
|---|---|---|---|
| `draft` | none | `pending` only | none |
| `research_only` | `advisor` | `pending`, `active` | none |
| `consultable` | `advisor` | `pending`, `active` | none |
| `paper_owner` | `advisor`, `paper_owner` | `pending`, `active` | `paper` |
| `live_owner` | `advisor`, `paper_owner`, `live_owner` | `pending`, `active` | `paper`, `canary`, `live` |
| `frozen` | none (existing bindings suspended) | `suspended` | none (existing bindings frozen) |
| `retired` | none | `revoked` | none |

### 6.2 Audit Reference Chain

Every runtime event must carry sufficient identifiers to trace back to the persona registry:

```
Runtime Event
  ├─ artifact_id        → approved artifact in registry
  ├─ deployment_stage   → RuntimeBinding.deployment_mode
  ├─ plan_id            → DeploymentPlan (intent)
  ├─ runtime_binding_id → RuntimeBinding (actual running state)
  ├─ persona_capital_binding_id → PersonaCapitalBinding (governance admissibility)
  ├─ persona_id         → Persona registry object (who)
  ├─ session_id         → SessionPersona (which execution context)
  └─ capability_snapshot_id → effective capabilities at session start
```

### 6.3 Multi-Persona Coexistence on a Single Pool

Per `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §9:

- Multiple `advisor` bindings per pool: **allowed**
- Multiple `paper_owner` bindings per pool: **allowed**
- Multiple `live_owner` bindings per pool: **NOT allowed** (single-live-owner rule)
- Multiple personas contributing to a single runtime: must be resolved upstream via
  judge / aggregator / committee synthesis → single approved artifact → single RuntimeBinding

### 6.4 Rollback and Persona Ownership

When a rollback occurs:

| Rollback Type | Persona Impact |
|---|---|
| `replace` | Same persona or different persona; artifact hot-swapped. Session may continue if capability snapshot is compatible. |
| `pause_then_replace` | Current persona session drained (no new entries). New persona session starts after pause. |
| `liquidate_then_replace` | Current persona session terminated. New persona session starts with clean state. |

The new `RuntimeBinding` may reference a different `persona_capital_binding_id` if the rollback
sponsors a different persona. The `persona_id` in the audit chain changes, but position lineage
(`current_managed_by_binding_id`) remains continuous at the pool level.

---

## 7. Gaps and Open Questions for PER-001 Contract Lock

These items are identified during mapping and should be resolved when PER-001 locks the
formal contract. They are **non-blocking for this prep slice**.

### 7.1 Session Persona → RuntimeBinding Reference

**Current state:** SessionPersona schema has no explicit `runtime_binding_id` field.

**Recommendation:** Add `runtime_binding_id` as an optional field to SessionPersona
for audit continuity. This allows any session's events to be traced back to the
governance binding without requiring a join through the deployment plan.

### 7.2 Capability Snapshot → Deployment Scope

**Current state:** CapabilitySnapshot does not include `effective_deployment_scope`.

**Recommendation:** The capability snapshot should include the resolved deployment
scope ceiling (derived from the active PersonaCapitalBinding) so the session knows
its maximum deployable stage at startup, not just its tool/workflow permissions.

### 7.3 Persona Lifecycle → Binding Status Sync

**Current state:** Persona `lifecycle_state` transitions and binding `status` transitions
are independently managed.

**Recommendation:** Define explicit cascade rules. E.g., when persona transitions to
`frozen`, all active bindings for that persona should transition to `suspended`.
When persona transitions to `retired`, bindings should transition to `revoked`.

### 7.4 RuntimeBinding → Session Persona Back-Reference

**Current state:** RuntimeBinding has no reference to active session(s).

**Recommendation:** RuntimeBinding metadata should carry `active_session_ids[]` for
operator visibility. This is a read-only projection, not a write authority change.

### 7.5 DeploymentPlan Binding Reference Naming Drift

**Current state:** `DeploymentPlan` currently uses `binding_id`, while `RuntimeBinding`
uses `persona_capital_binding_id` for the same governance edge.

**Recommendation:** PER-001 or a follow-on contract cleanup should either rename
`DeploymentPlan.binding_id` to `persona_capital_binding_id` or explicitly document
canonical aliasing between the two names. Telemetry, lineage, and persona packets
should not treat them as separate relationships.

---

## 8. Reviewer Packet (for Codex)

### 8.1 What to Review

This prep slice produces a **field mapping and resolution checklist**, not a contract change.
The reviewer should verify:

1. **Mapping correctness:** All field mappings in §4 are consistent with CAP-001, DEP-001, RUN-001, and RUN-001A source artifacts.
2. **Resolution order:** The checklist in §5 correctly orders dependencies (persona → binding → plan → runtime → session → runtime instance).
3. **Lifecycle matrix:** The compatibility matrix in §6.1 does not contradict any L1 policy.
4. **Audit chain:** The reference chain in §6.2 carries sufficient identifiers for full provenance.
5. **Gap analysis:** The open items in §7 are correctly identified as non-blocking prep observations.

### 8.2 Expected Outcome for PER-001

Once this mapping is approved, PER-001 should:
1. Formalize the `SessionPersona.runtime_binding_id` field addition (§7.1)
2. Formalize the capability snapshot scope carry (§7.2)
3. Formalize the lifecycle cascade rules (§7.3)
4. Update `PERSONA_RUNTIME_MODEL.md` or create a new L1 contract section as needed
5. Update `RuntimeBinding` metadata conventions for session back-references (§7.4)
6. Resolve `DeploymentPlan.binding_id` vs `RuntimeBinding.persona_capital_binding_id` naming drift (§7.5)

### 8.3 Artifacts to Inspect

| Artifact | What to Check |
|---|---|
| `PERSONA_RUNTIME_MODEL.md` | Three-layer model; session field inventory |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Binding/deployment/runtime semantics; §16 status mapping; §19 write authority |
| `services/control-plane/governance/persona_capital_binding.py` | BindingRole, DeploymentScope, store enforcement |
| `services/control-plane/governance/persona_capital_binding.schema.json` | Schema fields and enums |
| `services/control-plane/governance/deployment_plan.contract.md` | Current DeploymentPlan field names and binding linkage |
| `services/control-plane/governance/deployment_plan.schema.json` | Machine-readable DeploymentPlan field shape |
| `services/execution/runtime-manager/contract.md` | RuntimeBinding pre-conditions, references, lifecycle |
| `services/execution/runtime-manager/runtime_binding.py` | RuntimeBinding field names and status enum |
| `services/execution/runtime-manager/runtime_binding.schema.json` | Machine-readable RuntimeBinding field shape |
| `services/execution/runtime-manager/authority_matrix.md` | Write authority boundaries |
| `services/execution/runtime-manager/rollback_action_matrix.md` | Rollback types and position treatment |

---

## 9. Conclusion

This mapping document establishes the complete field-level correspondence between:

- **Persona registry model** (who the persona is)
- **PersonaCapitalBinding** (which pools it may serve and at what scope ceiling)
- **DeploymentPlan** (the approved deployment intent)
- **RuntimeBinding** (what is actually running)
- **Session Persona** (the execution context)
- **Runtime Persona** (the live agent instance)

The key insight is that **persona identity flows through governance bindings, not directly
to runtime**. Every runtime action attributable to a persona must be traceable through:

```
persona_id → PersonaCapitalBinding.binding_id → DeploymentPlan.binding_id → RuntimeBinding.persona_capital_binding_id → session_id → runtime events
```

This chain is the foundation upon which PER-001 will lock the formal persona runtime contract.

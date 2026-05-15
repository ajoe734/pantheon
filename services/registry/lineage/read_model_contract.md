# Lineage Read-Model Aggregation Contract

**Task:** `LIN-001`
**Owner:** Codex
**Reviewer:** Claude
**Status:** DRAFT — normalized lineage edge inventory and derived read-model contract

---

## 1. Purpose

`LIN-001` locks two things for the Phase 3 lineage backbone:

1. the **normalized cross-plane edge inventory** that services must write as their
   own source-of-truth fields
2. the **derived-only read-model contract** that assembles those edges into a
   stable payload for telemetry, incident, evolution, and BFF consumers

This document does **not** redefine write ownership. It defines how the already
owned objects line up into one read contract without creating a second truth
source.

---

## 2. Canonical Inputs

This contract refines, but does not override, the following canonical inputs:

| Source | Why it matters |
|---|---|
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | L1 policy for normalized-edge write path, derived-only projection, freshness, and SLA |
| `TARGET_ARCHITECTURE.md` | cross-plane ownership boundaries |
| `services/registry/lineage/LIN_001A_PREP_PACKET.md` | fixed capital/deployment/runtime/telemetry semantic edge inventory and benchmark corpus |
| `services/telemetry/telemetry_event.schema.json` | canonical telemetry raw-event field names after TEL-001 |
| `services/execution/runtime-manager/runtime_binding.schema.json` | canonical execution-plane binding truth |
| `services/control-plane/governance/deployment_plan.schema.json` | canonical deployment-plan field names, including the `binding_id` drift |
| `services/control-plane/bff/APP_001C_QUERY_CONTRACT_OUTLINE.md` | downstream query consumer expectations for lineage surfaces |

---

## 3. Scope

The end-to-end lineage chain for v1 is:

```text
SourceRecord
-> StrategySpec
-> ExperimentRun
-> CandidateArtifact / AllocationPolicyArtifact
-> ApprovalDecision
-> DeploymentPlan
-> RuntimeBinding
-> TelemetryEvent / IncidentCase / Postmortem / EvolutionDecision
```

`LIN-001` locks the **shape** of this chain. It does not require every service
to reconstruct the full chain on write.

---

## 4. Normalized Edge Inventory

The following semantic edges are the cross-plane v1 inventory. The semantic edge
id is stable even if one physical field is later renamed for clarity.

| Semantic edge id | From | To | Physical field today | Owner |
|---|---|---|---|---|
| `strategy_spec.source_record` | `StrategySpec` | `SourceRecord` | `StrategySpec.source_id` | Research / source ingest |
| `experiment_run.strategy_spec` | `ExperimentRun` | `StrategySpec` | `ExperimentRun.strategy_id` | Registry / experiment |
| `candidate_artifact.experiment_run` | `CandidateArtifact` | `ExperimentRun` | `CandidateArtifact.run_id` | Registry / experiment |
| `approval_decision.registry_target` | `ApprovalDecision` | governed artifact | `ApprovalDecision.target_id` | Governance |
| `deployment_plan.artifact` | `DeploymentPlan` | governed artifact | `DeploymentPlan.artifact_id` | Governance |
| `deployment_plan.capital_pool` | `DeploymentPlan` | `CapitalPool` | `DeploymentPlan.capital_pool_id` | Governance |
| `deployment_plan.persona_binding` | `DeploymentPlan` | `PersonaCapitalBinding` | `DeploymentPlan.binding_id` | Governance |
| `runtime_binding.artifact` | `RuntimeBinding` | governed artifact | `RuntimeBinding.artifact_id` | Runtime manager |
| `runtime_binding.capital_pool` | `RuntimeBinding` | `CapitalPool` | `RuntimeBinding.capital_pool_id` | Runtime manager |
| `runtime_binding.deployment_plan` | `RuntimeBinding` | `DeploymentPlan` | `RuntimeBinding.plan_id` | Runtime manager |
| `runtime_binding.persona_binding` | `RuntimeBinding` | `PersonaCapitalBinding` | `RuntimeBinding.persona_capital_binding_id` | Runtime manager |
| `runtime_binding.rollback_parent` | `RuntimeBinding` | `RuntimeBinding` | `RuntimeBinding.rollback_parent` | Runtime manager |
| `telemetry_event.runtime_binding` | `TelemetryEvent` | `RuntimeBinding` | `TelemetryEvent.binding_id` | Telemetry ingest |
| `telemetry_event.deployment_plan` | `TelemetryEvent` | `DeploymentPlan` | `TelemetryEvent.plan_id` | Telemetry ingest |
| `telemetry_event.capital_pool` | `TelemetryEvent` | `CapitalPool` | `TelemetryEvent.capital_pool_id` | Telemetry ingest |
| `telemetry_event.persona_binding` | `TelemetryEvent` | `PersonaCapitalBinding` | `TelemetryEvent.persona_capital_binding_id` | Telemetry ingest |
| `incident_case.runtime_binding` | `IncidentCase` | `RuntimeBinding` | `IncidentCase.binding_id` | Incident domain |
| `postmortem.incident_case` | `Postmortem` | `IncidentCase` | `Postmortem.incident_id` | Incident domain |
| `evolution_decision.postmortem` | `EvolutionDecision` | `Postmortem` | `EvolutionDecision.linked_postmortem_id` | Evolution governance |

Rules:

- every row above is a **formal edge**, not an optional JSON hint
- owner services may cache denormalized lineage payloads, but they must still
  write the formal edge fields
- downstream consumers must treat the semantic edge id as the contract, not the
  shortest raw field name

---

## 5. Raw-To-Semantic Normalization Rules

The read model must expose **disambiguated semantic names** so composed views do
not have to guess whether a raw `binding_id` refers to a runtime binding or a
persona-capital binding.

| Raw field location | Semantic read-model field | Rule |
|---|---|---|
| `DeploymentPlan.binding_id` | `persona_capital_binding_id` | same logical edge; raw field name is governance-local |
| `RuntimeBinding.binding_id` | `runtime_binding_id` | read model must disambiguate runtime binding ids from persona binding ids |
| `TelemetryEvent.binding_id` | `runtime_binding_id` | physical raw-event field remains `binding_id`; read projection exposes `runtime_binding_id` |
| `TelemetryEvent.plan_id` | `deployment_plan_id` | projection uses object-specific name |
| `TelemetryEvent.environment` | `deployment_stage` | `environment` is compatibility alias only; projection exposes canonical stage |
| `TelemetryEvent.target.artifact_version` | `artifact_version_hint` | compatibility duplicate only; top-level `artifact_version` remains authoritative if both exist |

Rules:

- raw owner tables keep their approved field names until a dedicated rename task
  lands
- read-model payloads normalize those names into object-specific semantic refs
- if both raw and semantic alias fields are present but disagree, the projection
  must surface a conflict marker rather than silently picking one and hiding the drift

---

## 6. Derived Read-Record Contract

The minimal derived record emitted into the lineage read-model should look like:

```text
record_type                 # e.g. telemetry_event
record_id                   # event_id / plan_id / binding_id / incident_id
derived_only = true
created_at

strategy_id
registry_id
promotion_state

runtime_binding_id
deployment_plan_id
capital_pool_id
persona_capital_binding_id
runtime_id

artifact_id
artifact_version
artifact_ref                # "{artifact_id}@{artifact_version}" when both exist
deployment_stage
trace_id
request_id
lineage_ref

conflict_markers[]
```

Notes:

- this is a **projection record**, not a write-owned domain object
- not every source object will populate every field
- telemetry-derived records are allowed to emit a partial view as long as the
  missing fields are absent because the source object truly did not carry them,
  not because the projection invented defaults

---

## 7. Summary Projection Contract

`lineage_projection` rows and `lineage_summary_json` payloads must both be
derived from normalized edges and use the same summary envelope:

```text
target_type
target_id
derived_only = true
projection_updated_at

upstream_chain[]
downstream_chain[]
conflict_markers[]

refs:
  strategy_ids[]
  registry_ids[]
  runtime_binding_ids[]
  deployment_plan_ids[]
  capital_pool_ids[]
  persona_capital_binding_ids[]
  artifact_refs[]
  trace_ids[]
```

Required properties:

- `target_type` + `target_id` identify the aggregate being summarized
- `derived_only=true` must be explicit
- `projection_updated_at` must be shown to the caller
- `conflict_markers[]` is where alias drift, stage mismatches, orphan telemetry,
  or rollback discontinuities are surfaced

`lineage_summary_json` may add UI-oriented nesting, but it must not add fields
that pretend to be authoritative beyond the normalized edges.

---

## 8. Query Families And SLA Mapping

`LIN-001` adopts the query-family split already prepared in `LIN-001A`.

| Query family | Target shape | SLA bucket |
|---|---|---|
| `runtime_binding_projection` | `runtime_lineage_summary` | synchronous summary |
| `capital_pool_projection` | pool-scoped lineage summary | synchronous summary |
| `telemetry_event_trace` | one-event normalized trace | synchronous summary |
| `source_runtime_telemetry_trace` | operator-facing trace from source / strategy / experiment through approval, deployment, runtime, broker-order lifecycle, telemetry, incident, postmortem, and evolution refs | synchronous summary |
| `forensic_plan_trace` | rollback-aware full plan trace | forensic / async-capable |

Rules:

- synchronous summaries must stay within the L1 freshness and latency targets
- `source_runtime_telemetry_trace` is still a derived read model; missing source,
  artifact, approval, broker-order, incident, or evolution nodes must appear in
  `missing_edges[]` / `conflict_markers[]` rather than being inferred
- forensic traces may reconstruct more of the graph and take longer
- no consumer is allowed to bypass the read model and re-invent deep multi-table
  joins in the BFF just because one screen needs another field

---

## 9. Derived-Only Invariants

The lineage read model is an acceleration layer only.

It must obey all of the following:

1. it never becomes the only persisted source of a lineage edge
2. it can be fully rebuilt from normalized owner records
3. it records freshness through `projection_updated_at`
4. it surfaces conflict markers instead of mutating owner truth to make the
   projection look consistent
5. it never writes back normalized edge fields into owner services

---

## 10. Downstream Consumers

This contract is the input for:

- `INC-001` incident/postmortem evidence attachment
- `LIN-002` optimized read-service implementation and benchmark validation
- `EVO-003` evidence-linked `EvolutionDecision`
- `APP-001` lineage/BFF surfaces

The expectation is simple:

- owners write normalized edges
- `LIN-001` defines the one derived aggregation contract
- `LIN-002` optimizes that contract, not a different shape

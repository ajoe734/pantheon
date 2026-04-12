# INC-001: Incident and Postmortem Backbone Contract

**Task:** `INC-001`
**Owner:** Claude
**Reviewer:** Copilot
**Phase:** Phase 3: Telemetry, Lineage, and Incident Backbone
**Status:** DRAFT

---

## 1. Purpose

INC-001 defines the canonical `IncidentCase` and `Postmortem` backbone objects
for Pantheon.  These objects are the bridge between runtime execution evidence
(TEL-001) and the governance/evolution decision chain (EVO-003, EVO-004).

Every incident and postmortem MUST attach to the runtime binding identity,
deployment stage, and lineage evidence so that forensic queries can reconstruct
the full chain without cross-plane joins at query time.

---

## 2. Canonical Inputs

| Source | Role |
|---|---|
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | L1 lineage edge policy, traceability rules (§9), telemetry canonical store strategy |
| `services/registry/lineage/read_model_contract.md` | LIN-001 normalized edge inventory and derived read-model contract |
| `services/execution/runtime-manager/runtime_binding.schema.json` | RuntimeBinding canonical field names |
| `services/telemetry/capture.py` | TEL-001 binding evidence fields injected into every telemetry event |

---

## 3. Normalized Lineage Edges

INC-001 instantiates two of the LIN-001 formal edges:

| Semantic edge id | From | To | Physical field |
|---|---|---|---|
| `incident_case.runtime_binding` | `IncidentCase` | `RuntimeBinding` | `IncidentCase.binding_id` |
| `postmortem.incident_case` | `Postmortem` | `IncidentCase` | `Postmortem.incident_id` |

The third downstream edge is owned by EVO-003:

| Semantic edge id | From | To | Physical field |
|---|---|---|---|
| `evolution_decision.postmortem` | `EvolutionDecision` | `Postmortem` | `EvolutionDecision.linked_postmortem_id` |

`Postmortem.linked_evolution_decision_id` is the reverse-link slot for
`IncidentStore.link_evolution_decision()` — set by EVO-003 after an
`EvolutionDecision` references this postmortem.

---

## 4. IncidentCase Object

### 4.1 Required fields

| Field | Type | Description |
|---|---|---|
| `incident_id` | string | Unique identifier (UUID recommended) |
| `title` | string | Human-readable title |
| `status` | enum | `open` \| `investigating` \| `resolved` \| `closed` |
| `severity` | enum | `critical` \| `high` \| `medium` \| `low` |
| `created_at` | ISO-8601 UTC | Incident creation timestamp |
| `binding_id` | string | **Formal lineage edge** → `RuntimeBinding` |
| `deployment_stage` | enum | `paper` \| `canary` \| `live` \| `frozen` |
| `deployment_plan_id` | string | `DeploymentPlan` reference |
| `capital_pool_id` | string | `CapitalPool` reference |
| `persona_capital_binding_id` | string | `PersonaCapitalBinding` reference — governance admissibility proof |
| `artifact_id` | string | Governed artifact under execution |
| `artifact_version` | string | Specific artifact version |
| `runtime_id` | string | LEAN runtime / container / worker process |
| `trace_id` | string | Trace identifier (L1 traceability, required) |

### 4.2 Optional fields

| Field | Type | Description |
|---|---|---|
| `resolved_at` | ISO-8601 UTC | Required when status is `resolved` or `closed` |
| `telemetry_event_ids` | `string[]` | Triggering or evidence TelemetryEvent IDs |
| `evidence_summary` | string | Human-readable evidence summary |
| `lineage_ref` | string | Composite ref e.g. `"{artifact_id}@{artifact_version}"` |

### 4.3 Status lifecycle

```
open → investigating → resolved → closed
```

`resolved_at` is auto-set by `IncidentStore.update_incident_status()` when
transitioning to `resolved` or `closed`.

---

## 5. Postmortem Object

### 5.1 Required fields

| Field | Type | Description |
|---|---|---|
| `postmortem_id` | string | Unique identifier (UUID recommended) |
| `title` | string | Human-readable title |
| `status` | enum | `draft` \| `review` \| `approved` \| `published` |
| `created_at` | ISO-8601 UTC | Postmortem creation timestamp |
| `incident_id` | string | **Formal lineage edge** → `IncidentCase` |
| `binding_id` | string | Propagated from `IncidentCase.binding_id` |
| `deployment_stage` | enum | Propagated from `IncidentCase.deployment_stage` |
| `deployment_plan_id` | string | Propagated from `IncidentCase.deployment_plan_id` |
| `capital_pool_id` | string | Propagated from `IncidentCase.capital_pool_id` |
| `persona_capital_binding_id` | string | Propagated from `IncidentCase.persona_capital_binding_id` |
| `artifact_id` | string | Propagated from `IncidentCase.artifact_id` |
| `artifact_version` | string | Propagated from `IncidentCase.artifact_version` |
| `runtime_id` | string | Propagated from `IncidentCase.runtime_id` |
| `trace_id` | string | Propagated from `IncidentCase.trace_id` |
| `root_cause` | string | Root cause analysis |

### 5.2 Optional fields

| Field | Type | Description |
|---|---|---|
| `published_at` | ISO-8601 UTC | Required when status is `published` |
| `contributing_factors` | `string[]` | Contributing factors |
| `timeline` | `object[]` | Ordered event timeline (`ts`, `description`, `actor`) |
| `action_items` | `string[]` | Follow-up action items |
| `author_ids` | `string[]` | Postmortem authors |
| `linked_evolution_decision_id` | string | Set by EVO-003 — reverse link for `evolution_decision.postmortem` edge |

### 5.3 Status lifecycle

```
draft → review → approved → published
```

`published_at` is auto-set by `IncidentStore.update_postmortem_status()` when
transitioning to `published`.

---

## 6. Write Authority

| Object | Write authority |
|---|---|
| `IncidentCase` | Incident domain only |
| `Postmortem` | Incident domain only |
| `Postmortem.linked_evolution_decision_id` | Set by EVO-003 via `IncidentStore.link_evolution_decision()` |

---

## 7. IncidentStore Referential Integrity

`IncidentStore.create_postmortem()` enforces that the referenced `incident_id`
exists in the same store before accepting the postmortem.  It also enforces
that every propagated evidence field on `Postmortem` exactly matches the
referenced `IncidentCase`. This prevents both orphaned postmortems and
forensically inconsistent evidence snapshots.

---

## 8. Denormalization Policy

The deployment evidence fields on both `IncidentCase` and `Postmortem`
(binding_id, deployment_stage, deployment_plan_id, capital_pool_id,
persona_capital_binding_id, artifact_id, artifact_version, runtime_id,
trace_id) are **denormalized copies** of the RuntimeBinding state at the time
of incident creation.

- The formal edge (`binding_id → RuntimeBinding`) is the write-owned truth anchor.
- The denormalized fields accelerate incident/postmortem read queries without
  requiring a cross-plane RuntimeBinding join at query time.
- If the canonical RuntimeBinding record changes after incident creation, the
  denormalized snapshot in the incident is **not** updated — it represents the
  state at time of incident, which is the forensically relevant value.

---

## 9. Lineage Read Model Integration

Per the LIN-001 read-model contract, the lineage read model maintains an
`incident_lineage_summary` projection.  This projection is assembled from the
formal edges, not from the denormalized snapshot fields.

Downstream consumers of lineage data (BFF, audit, forensic tools) MUST use
the lineage read model API rather than querying `IncidentCase` directly across
service boundaries.

---

## 10. Downstream Consumers

| Consumer | What they need |
|---|---|
| `EVO-003` | `linked_postmortem_id` for `EvolutionDecision.linked_postmortem_id` edge; reverse-set via `IncidentStore.link_evolution_decision()` |
| `EVO-004` | Incident/postmortem status for operational evolution boundaries |
| `APP-002` | Operator-facing incident/evolution surfaces |
| Lineage read model | `incident_case.runtime_binding` and `postmortem.incident_case` normalized edges |

---

## 11. Acceptance Criteria (INC-001)

- [x] `IncidentCase` carries `binding_id` (formal edge → RuntimeBinding)
- [x] `IncidentCase` carries `deployment_stage`, `deployment_plan_id`, `capital_pool_id`, `persona_capital_binding_id`, `artifact_id`, `artifact_version`, `runtime_id` (deployment evidence)
- [x] `IncidentCase` carries `trace_id` (L1 traceability)
- [x] `Postmortem` carries `incident_id` (formal edge → IncidentCase)
- [x] `Postmortem` propagates `binding_id`, `deployment_stage`, `deployment_plan_id`, `capital_pool_id`, `persona_capital_binding_id`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id`
- [x] `Postmortem` has `linked_evolution_decision_id` slot for EVO-003
- [x] JSON schemas cover all required fields with enum constraints
- [x] `IncidentStore` enforces referential integrity (postmortem requires incident)
- [x] Unit tests: 36+ checks across object construction, validation, store operations, persistence
- [x] Smoke tests: 8 groups — schema, lifecycle, lineage evidence, referential integrity, evolution link, persistence

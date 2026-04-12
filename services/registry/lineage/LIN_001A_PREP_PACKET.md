# LIN-001A Prep Packet

Last updated: 2026-04-10  
Task: `LIN-001A`  
Owner: Codex  
Reviewer: Qwen  
Status: done

---

## 1. Purpose

`LIN-001A` is a parallel prep slice for `LIN-001`.

It does not define the final lineage read-model contract. It prepares the
capital/deployment/runtime/telemetry portion of that work so `LIN-001` can
start from a fixed edge inventory, a machine-readable corpus, and a repeatable
benchmark method instead of re-scoping the same slice again.

This packet ships three artifacts:

- `services/registry/lineage/LIN_001A_PREP_PACKET.md`
- `services/registry/lineage/lin001a_benchmark_corpus.json`
- `services/registry/lineage/benchmark_harness.py`

---

## 2. Canonical Inputs

This packet is derived from the current canonical sources for this slice:

| Source | Why it matters |
|---|---|
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | Defines normalized-edge write path, read-model ownership, projection semantics, and SLA buckets |
| `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` | Defines telemetry canonical-store shape and ingest ownership |
| `services/control-plane/governance/capital_pool.contract.md` | Defines `CapitalPool` and `PersonaCapitalBinding` as governed objects |
| `services/control-plane/governance/deployment_plan.contract.md` | Defines `DeploymentPlan` as the stage-transition object consumed by telemetry/lineage |
| `services/execution/runtime-manager/contract.md` | Defines `RuntimeBinding` as execution-plane truth with rollback lineage |
| `services/telemetry/TEL_001A_FIELD_PACKET.md` | Defines the telemetry evidence fields and the `binding_id` field currently proposed for telemetry envelopes |
| `services/control-plane/persona/review_per001a_codex_approved_zh.md` | Confirms the current naming drift between `DeploymentPlan.binding_id` and `RuntimeBinding.persona_capital_binding_id` |

---

## 3. Scope Boundary

This prep slice is intentionally limited to the objects below:

- `CapitalPool`
- `PersonaCapitalBinding`
- `DeploymentPlan`
- `RuntimeBinding`
- `TelemetryEvent`

It does not attempt to formalize the full upstream research/registry chain from
`SourceRecord -> StrategySpec -> ExperimentRun -> CandidateArtifact`.

For this prep slice those upstream references stay as carried scalar evidence:

- `artifact_id`
- `artifact_version`
- `approval_decision_id`
- `strategy_id`
- `runtime_id`
- `trace_id`
- `request_id`

That boundary is deliberate. `LIN-001` should be able to plug this packet into
the broader end-to-end lineage chain without reopening the already-fixed
capital/runtime/telemetry slice.

---

## 4. Naming Drift That Must Not Be Re-Litigated In LIN-001

Two field-name drifts already exist across the current approved packets:

| Logical edge | Current physical field(s) | Source |
|---|---|---|
| Deployment plan is governed by a persona-capital binding | `DeploymentPlan.binding_id` | `deployment_plan.schema.json`, `PER-001A` review |
| Telemetry event points to the runtime binding that emitted it | `TelemetryEvent.binding_id` in `TEL-001A`; `TelemetryEvent.runtime_binding_id` in L1 storage policy | `TEL_001A_FIELD_PACKET.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` |

To keep `LIN-001` focused on the read-model contract rather than field-name
debates, this prep packet publishes **semantic edge ids** that stay stable even
if the physical column names are later renamed.

---

## 5. Semantic Edge Inventory

The inventory below is the candidate normalized-edge set for the
capital/deployment/runtime/telemetry slice.

| Semantic edge id | From | To | Physical field(s) today | Class | Owner | Why it exists |
|---|---|---|---|---|---|---|
| `persona_binding.capital_pool` | `PersonaCapitalBinding` | `CapitalPool` | `PersonaCapitalBinding.capital_pool_id -> CapitalPool.pool_id` | normalized_fk | Capital Pool Plane | lets pool views and admissibility checks stay joinable |
| `deployment_plan.capital_pool` | `DeploymentPlan` | `CapitalPool` | `DeploymentPlan.capital_pool_id -> CapitalPool.pool_id` | normalized_fk | Governance Plane | anchors stage transitions to one pool |
| `deployment_plan.persona_binding` | `DeploymentPlan` | `PersonaCapitalBinding` | `DeploymentPlan.binding_id -> PersonaCapitalBinding.binding_id` | normalized_fk with alias drift | Governance Plane | links a plan back to its governance authority |
| `runtime_binding.capital_pool` | `RuntimeBinding` | `CapitalPool` | `RuntimeBinding.capital_pool_id -> CapitalPool.pool_id` | normalized_fk | Runtime Manager | preserves single-runtime and pool-scoped traceability |
| `runtime_binding.deployment_plan` | `RuntimeBinding` | `DeploymentPlan` | `RuntimeBinding.plan_id -> DeploymentPlan.plan_id` | normalized_fk | Runtime Manager | makes runtime state auditable against the governing plan |
| `runtime_binding.persona_binding` | `RuntimeBinding` | `PersonaCapitalBinding` | `RuntimeBinding.persona_capital_binding_id -> PersonaCapitalBinding.binding_id` | normalized_fk with alias drift | Runtime Manager | preserves the governance admissibility proof at execution time |
| `runtime_binding.rollback_parent` | `RuntimeBinding` | `RuntimeBinding` | `RuntimeBinding.rollback_parent -> RuntimeBinding.binding_id` | self_lineage | Runtime Manager | preserves replace/rollback continuity across binding cutovers |
| `telemetry_event.runtime_binding` | `TelemetryEvent` | `RuntimeBinding` | `TelemetryEvent.binding_id` today; semantic alias `runtime_binding_id` | normalized_fk with alias drift | Telemetry ingest | lets telemetry join back to binding truth without heuristic inference |
| `telemetry_event.deployment_plan` | `TelemetryEvent` | `DeploymentPlan` | `TelemetryEvent.plan_id -> DeploymentPlan.plan_id` | normalized_fk | Telemetry ingest | lets incident and lineage queries jump straight from event to plan |
| `telemetry_event.capital_pool` | `TelemetryEvent` | `CapitalPool` | `TelemetryEvent.capital_pool_id -> CapitalPool.pool_id` | normalized_fk | Telemetry ingest | supports pool-scoped dashboards and reconciliation without multi-hop recovery |
| `telemetry_event.persona_binding` | `TelemetryEvent` | `PersonaCapitalBinding` | `TelemetryEvent.persona_capital_binding_id -> PersonaCapitalBinding.binding_id` | normalized_fk | Telemetry ingest | keeps governance scope visible from raw telemetry |
| `telemetry_event.artifact_guard` | `TelemetryEvent` | runtime snapshot carried by `RuntimeBinding` | `(artifact_id, artifact_version)` | denormalized_guard | Telemetry ingest | validates the event against the binding snapshot without replacing the binding edge |
| `telemetry_event.runtime_guard` | `TelemetryEvent` | runtime snapshot carried by `RuntimeBinding` | `runtime_id` | denormalized_guard | Telemetry ingest | catches emitter/binding mismatch without inventing a separate Runtime object |

### What this means for LIN-001

`LIN-001` should treat the table above as the fixed semantic edge set for this
slice:

- keep the semantic edge ids
- choose the final physical field names later if needed
- do not reopen scope to invent new capital/runtime/telemetry objects

---

## 6. Machine-Readable Corpus Shape

The corpus lives at:

- `services/registry/lineage/lin001a_benchmark_corpus.json`

It contains:

- `metadata`: task identity, scope, projection timestamp, alias table
- `semantic_edges`: the edge ids above, in machine-readable form
- `node_sets`: fixture records for the five in-scope object families
- `query_families`: the benchmarked query classes and their SLA bucket mapping
- `benchmark_cases`: concrete cases with expected chain ids and marker ids
- `benchmark_config`: default warmup and measurement settings

Current fixture size:

- 2 capital pools
- 3 persona-capital bindings
- 4 deployment plans
- 4 runtime bindings
- 8 telemetry events
- 4 query families
- 5 benchmark cases

The fixture is intentionally small enough to review by hand and stable enough
to remain the default contract corpus for `LIN-001` and `LIN-002`.

---

## 7. Query Families And SLA Buckets

The harness benchmarks four query families.

| Query family | Input | Output shape | SLA bucket | p95 budget | Rationale |
|---|---|---|---|---|---|
| `runtime_binding_projection` | `binding_id` | projection summary for one binding with linked pool, plan, governance binding, runtime ref, telemetry events, and rollback markers | sync projection summary | `<= 500ms` | mapped to the existing synchronous lineage-summary class in `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` §5.3/§5.6; no new SLA is invented |
| `capital_pool_projection` | `pool_id` | pool-scoped projection summary covering bindings, plans, runtime refs, and telemetry events | sync projection summary | `<= 500ms` | same bucket as above; this is a read-optimized summary, not a forensic traversal |
| `telemetry_event_trace` | `event_id` | event-scoped trace proving pool, plan, binding, runtime, and artifact context | sync projection summary | `<= 500ms` | event trace is still part of the projection/read path, not the async forensic path |
| `forensic_plan_trace` | `plan_id` | rollback-aware full trace across a plan, its bindings, runtime refs, and related telemetry | forensic full lineage | `<= 5000ms` | directly mapped to the `forensic full lineage query` bucket in L1 §5.6 |

The important point is the SLA bucket mapping. `LIN-001A` does not add new
latency doctrine; it maps runtime/capital queries onto the already-approved
projection vs forensic split.

---

## 8. Repeatable Benchmark Method

The benchmark harness lives at:

- `services/registry/lineage/benchmark_harness.py`

Method:

1. Load the JSON corpus.
2. Build deterministic indexes by object id and foreign-key edge.
3. Resolve one benchmark case once and validate that all expected ids and
   marker ids are present in the query result.
4. Run a warmup loop.
5. Measure the query body with `time.perf_counter_ns()`.
6. Convert to milliseconds.
7. Compute p50 and p95 using the nearest-rank percentile over post-warmup
   samples.
8. Emit one JSON report with per-case metrics, per-family aggregate metrics,
   and pass/fail budget comparisons.

Important limitation:

- this harness measures the repeatable **query contract and traversal shape**
  for the corpus in memory
- it does **not** claim to be the final database or service benchmark for
  `LIN-002`
- `LIN-002` should reuse the same corpus and query families, then swap the
  resolver implementation from in-memory traversal to the real read service
  path

---

## 9. How LIN-001 Should Consume This Packet

`LIN-001` can start from this packet without redefining read-model scope:

1. Use the semantic edge ids in Section 5 as the canonical inventory for the
   capital/deployment/runtime/telemetry slice.
2. Preserve the alias table in Section 4 so the read model can tolerate the
   current physical field-name drift.
3. Reuse the query families in Section 7 as the first read-model contract for
   `runtime_lineage_summary` and `capital_pool_lineage_summary`.
4. Keep this slice derived-only: projection is an acceleration layer and not a
   new truth source, exactly as L1 requires.

What `LIN-001` still needs to add later:

- the upstream research/registry lineage segment
- the final projection schema
- refresh/repair/replay mechanics
- the final physical field-name normalization decision

---

## 10. Follow-Ups Left Open On Purpose

These are real gaps, but they are not blockers for `LIN-001A`:

| Gap | Why not solved here | Owner lane |
|---|---|---|
| `DeploymentPlan.binding_id` vs `RuntimeBinding.persona_capital_binding_id` naming | `LIN-001A` fixes the semantic edge id but does not rename approved schemas | `PER-001` + `LIN-001` |
| `TelemetryEvent.binding_id` vs `TelemetryEvent.runtime_binding_id` naming | `TEL-001A` already proposed the field packet; `LIN-001A` only preserves the alias mapping | `TEL-001` + `LIN-001` |
| Final service-level benchmark against real storage/query path | out of scope for this prep slice; belongs to `LIN-002` | `LIN-002` |

---

## 11. Exit Criteria Coverage

`LIN-001A` acceptance is covered as follows:

| Acceptance | Where covered |
|---|---|
| candidate lineage edges across capital deployment runtime and telemetry are enumerated | Section 5 + `semantic_edges` in the JSON corpus |
| benchmark corpus shape and p95 measurement method are documented | Sections 6-8 + `benchmark_config` in the JSON corpus |
| LIN-001 can consume the prep package without redefining read-model scope | Sections 3, 4, and 9 |

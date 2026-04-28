# SD-LIN-TRACE-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `SD-LIN-TRACE-001` - Build source-to-runtime-to-telemetry lineage trace query  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Codex2`  
**Parent Status**: `review`  
**Sidecar Task**: `SD-LIN-TRACE-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Codex2`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-27`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 canonical truth, core
> contract truth, runtime / registry / governance implementation, or the parent
> execution record. It packages a reviewer-facing acceptance checklist,
> dependency map, verification evidence, and handoff notes for
> `SD-LIN-TRACE-001`.

## 1. Executive Summary

`SD-LIN-TRACE-001` is in `review` with `Codex` as owner and `Codex2` as
reviewer. The parent implementation adds the first operator-facing
`source_runtime_telemetry_trace` query to the telemetry lineage read service,
routes it through the telemetry HTTP API, documents the query family in the
lineage read-model contract, and covers the core service and HTTP behaviors
with targeted tests.

The implemented shape is a derived read model. It does not become the owner of
source, strategy, artifact, deployment, runtime, broker, telemetry, incident,
postmortem, or evolution truth. Missing referenced nodes are surfaced through
`missing_edges[]` and `conflict_markers[]` rather than inferred or silently
backfilled.

This sidecar summarizes the current acceptance evidence and makes the task
split explicit: `SD-LIN-TRACE-001` owns one replayable operator-facing trace
shape; `SD-RECON-001` owns deeper reconciliation lifecycle coverage;
`SD-SRC-EVIDENCE-001` owns governed source / evidence / search surfaces; and
later EP5 proof packet work should consume this trace without treating it as
live / canary execution proof.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable task board for parent / sidecar status, owner, reviewer, acceptance, and artifact paths |
| `.orchestrator/task-briefs/sd_lin_trace_001_sidecar_acceptance.md` | Confirms this helper is support-only and must hand off to `Codex2` |
| `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Defines the SD lineage gap, activation order, and registry / lineage acceptance shape |
| `docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json` | Shows the underlying EP4 / EP5 planning session is accepted and human-gate approved |
| `services/telemetry/lineage_read/service.py` | Implements `source_runtime_telemetry_trace`, query dispatch, missing-edge reporting, and derived-only payload assembly |
| `services/registry/lineage/read_model_contract.md` | Documents the query family and derived-only invariant for missing edges and conflict markers |
| `services/telemetry/main.py` | Exposes `/api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry` through the existing lineage query response path |
| `services/telemetry/lineage_read/test_service.py` | Covers full trace assembly, missing source edge surfacing, and missing `trace_id` validation |
| `services/telemetry/test_main_routes.py` | Covers HTTP 200 for the trace route and 404 for a missing trace target |

## 3. Parent Acceptance Checklist

| Parent acceptance target | Evidence to review | Status now |
|---|---|---|
| Operator can query one source-to-runtime-to-telemetry trace | `LineageProjection.source_runtime_telemetry_trace()` delegates to `_build_source_runtime_telemetry_trace()`; `LineageReadService.query()` accepts `query_family="source_runtime_telemetry_trace"` and requires `trace_id` | PASS |
| Trace joins source, strategy, experiment, artifact, approval, deployment plan, runtime binding, broker/order lifecycle, telemetry, incident, postmortem, and evolution refs | `_build_source_runtime_telemetry_trace()` walks telemetry events by `trace_id`, then derives source, deployment, runtime, broker lifecycle, incident, postmortem, and evolution chains; `test_query_source_runtime_telemetry_trace` asserts the expected refs | PASS |
| Read model remains derived-only and not a parallel truth source | Response sets `derived_only: True`; contract states missing source / artifact / approval / broker / incident / evolution nodes must surface in `missing_edges[]` / `conflict_markers[]` rather than being inferred | PASS |
| Missing IDs or missing edges are replayable and explicit | No telemetry for a trace returns `node_not_found`; missing referenced source data is asserted as `strategy_spec.source_record` in `missing_edges[]` and `missing_lineage_edge` in `conflict_markers[]` | PASS |
| HTTP route exposes the operator-facing query | `GET /api/telemetry/lineage/traces/<trace_id>/source-runtime-telemetry` calls `_lineage_query_response("source_runtime_telemetry_trace", trace_id=trace_id)` | PASS |
| Missing trace target maps to stable HTTP error semantics | `_lineage_query_response()` maps `node_not_found` conflict markers to `LINEAGE_TARGET_NOT_FOUND`; route tests assert 404 for a missing trace | PASS |
| Contract note names the query family and boundary | `services/registry/lineage/read_model_contract.md` includes `source_runtime_telemetry_trace` in Query Families And SLA Mapping and restates derived-only missing-edge rules | PASS |
| Targeted service and route tests pass | `pytest services/telemetry/lineage_read/test_service.py services/telemetry/test_main_routes.py -q` | PASS |

## 4. Verification Evidence

Command rerun from repo root on 2026-04-27 UTC for this sidecar packet:

1. `pytest services/telemetry/lineage_read/test_service.py services/telemetry/test_main_routes.py -q` - PASS, `39 passed in 0.84s`

Relevant implemented surface:

```text
services/telemetry/lineage_read/service.py
services/telemetry/lineage_read/test_service.py
services/telemetry/main.py
services/telemetry/test_main_routes.py
services/registry/lineage/read_model_contract.md
```

## 5. Dependency Map

### 5.1 Durable Task Dependencies

| Task | Relationship | Current read |
|---|---|---|
| `SD-LIN-TRACE-001` | parent task | Mainline source-to-runtime-to-telemetry derived trace implementation, currently in `review` |
| `SD-LIN-TRACE-001-SIDECAR-ACCEPTANCE` | support helper | Acceptance and dependency packet only; does not mutate canonical truth |
| `SD-RECON-001` | direct downstream task | Depends on `SD-LIN-TRACE-001`; should extend order, fill, cancel, position, paper-live drift, and alert closure reconciliation |
| `EP5-002-PACKET-PREP-001` | later proof packet prep | Depends on `SD-FND-002` and `SD-LIN-TRACE-001`; should consume trace refs in dry-run packet prep without placing live orders |
| `CROSS-REPO-SD-VERIFY-001` | later cross-repo verification | Depends on `SD-FND-002` and `SD-LIN-TRACE-001`; should verify frontend command authority, trace/error UX, runtime telemetry hooks, and no parallel authority paths |

### 5.2 Parallel SD Residual Lanes

| Task | Relationship | Boundary for this review |
|---|---|---|
| `SD-FND-001` | parallel foundation package lane | Shared trace / command primitives can be adopted later; this parent does not require foundation adoption to close |
| `SD-FND-002` | future adoption lane | Should connect shared command envelopes to BFF/runtime-manager paths before EP5 packet prep, but is outside this lineage trace parent |
| `SD-FND-003` | future durable primitives lane | Outbox / DLQ / schema-registry primitives can harden later replay paths; not required for this derived read model |
| `SD-SRC-EVIDENCE-001` | parallel source / evidence / search lane | Should govern source connectors and evidence bundles; this trace records source refs but does not validate evidence bundles |
| `SD-CONSULT-001` | parallel consultation domain lane | Consultation handoff and audit refs may become future lineage edges, but the current parent does not claim consultation integration |

### 5.3 Semantic Dependency Chain

| Dependency | Source | Why it matters |
|---|---|---|
| SD lineage gap definition | `docs/reviews/2026-04-27-sd-materializable-execution-task-packet.md` | Requires an operator-facing trace that joins source, strategy, experiment, artifact, approval, deployment plan, runtime binding, broker/order lifecycle, telemetry, and evolution refs as a derived read model |
| Accepted planning state | phase7 `planning-session.json` | Confirms the broader EP4 / EP5 planning context is accepted while EP5 live / canary execution remains gate-bound |
| Query-family contract | `services/registry/lineage/read_model_contract.md` | Places `source_runtime_telemetry_trace` in the synchronous summary bucket and forbids inference for missing edges |
| Derived read-model implementation | `services/telemetry/lineage_read/service.py` | Centralizes graph traversal and payload assembly in the lineage read service instead of adding BFF-specific joins |
| HTTP route boundary | `services/telemetry/main.py` | Reuses the existing lineage query response helper and preserves stable error mapping |
| Service and route tests | `services/telemetry/lineage_read/test_service.py`, `services/telemetry/test_main_routes.py` | Proves the trace can be built, missing edges are explicit, required params are enforced, and HTTP route behavior is stable |

## 6. Open Cautions for Review

| Caution | Why it matters |
|---|---|
| This sidecar is not the parent implementation | It only packages acceptance and dependency evidence for `Codex2` review |
| The trace is derived-only | It must not be treated as owner-written registry, source, runtime, broker, telemetry, incident, postmortem, or evolution truth |
| Reconciliation depth remains downstream | Order / fill / cancel / position / drift / alert closure lifecycle proof belongs to `SD-RECON-001` |
| Source evidence validation remains downstream | Source refs are carried, but governed SourceConnector / EvidenceBundle / SearchGateway validation belongs to `SD-SRC-EVIDENCE-001` |
| EP5 live / canary proof is not promoted | This trace can support later proof packets, but it does not authorize or prove live broker side effects |
| Corpus-backed behavior is a local acceptance surface | Current tests prove the read-model shape and HTTP route against test corpora; production data durability and cross-service ingestion remain later verification concerns |

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this packet as canonical SD-01 / SD-09 architecture truth | This file is support material only |
| Requiring this parent to finish `SD-RECON-001` lifecycle reconciliation | The materialization packet makes reconciliation a dependent follow-up after the trace shape exists |
| Requiring governed source connector or evidence-bundle validation here | That is the explicit scope of `SD-SRC-EVIDENCE-001` |
| Treating the trace route as a live / canary execution proof | EP5 live / canary execution remains behind explicit human approval and separate proof packet tasks |
| Adding BFF-local deep joins to satisfy review | The contract says consumers must not bypass the read model to re-invent deep multi-table joins |
| Mutating L1 docs, core contract truth, runtime registry, or governance implementation from this sidecar | Sidecar scope explicitly forbids canonical or runtime implementation changes |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/SD-LIN-TRACE-001/SD-LIN-TRACE-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited by sidecar | PASS | No L1 policy docs, contract docs, runtime registry, or governance implementation files were modified in this helper slice |
| Parent acceptance mapped to repo-current evidence | PASS | Sections 3 and 4 tie each acceptance item to concrete service, route, contract, and test evidence |
| Dependency chain is explicit | PASS | Section 5 maps downstream `SD-RECON-001`, later EP5 packet prep, cross-repo verification, and parallel SD residual lanes |
| Review caveats are bounded | PASS | Sections 6 and 7 separate this derived trace from reconciliation depth, source evidence validation, live proof, and canonical truth changes |

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for reviewer use as the acceptance / dependency packet for
`SD-LIN-TRACE-001` in its current `review` state.

What it gives you now:

1. a checklist that maps each parent acceptance criterion to concrete repo
   evidence
2. fresh verification that the targeted lineage read and telemetry route tests
   pass from the repo root (`39 passed`)
3. a dependency map showing which follow-on tasks should absorb reconciliation,
   source/evidence validation, foundation adoption, EP5 packet prep, and
   cross-repo verification
4. review guardrails that keep this helper support-only and prevent overclaiming
   canonical truth, live / canary readiness, source evidence governance, or full
   SD-09 reconciliation

Recommended reviewer stance now:

1. approve the sidecar if the packet accurately reflects the parent review
   surface and support-only boundary
2. review the parent task against the concrete lineage read-model, HTTP route,
   contract, and test evidence
3. keep reconciliation depth, evidence-bundle validation, foundation envelope
   adoption, and live / canary proof asks as follow-up work unless they are
   required by the parent acceptance text

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`SD-LIN-TRACE-001`. This file is a support artifact and does not modify
canonical truth.*

# LIN-002 Acceptance Packet (Sidecar)

**Parent Task**: `LIN-002` — Implement lineage read service performance path
**Parent Owner**: Qwen
**Parent Reviewer**: Claude
**Parent Status**: `review`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Qwen
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-10T13:57:31Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime implementations. It packages review evidence for the parent owner and reviewer while `LIN-002` remains in `review`.

---

## 1. Dependency Map

### 1.1 Formal Parent Dependency

| Dependency | Task ID | Status | Relevance to LIN-002 |
|---|---|---|---|
| Lineage read-model contract | `LIN-001` | done | Defines the one derived-only query shape that `LIN-002` must optimize rather than replace |

### 1.2 Evidence Prerequisites Used By This Packet

| Prerequisite | Task ID | Status | Why it matters |
|---|---|---|---|
| Telemetry stage and binding truth | `TEL-001` | done | Provides the canonical runtime/deployment/persona/binding refs that the lineage corpus and service traverse |
| Benchmark corpus prep slice | `LIN-001A` | done | Supplies the machine-readable corpus, query-family split, and SLA harness that `LIN-002` validates against |

### 1.3 Direct Downstream Task Dependencies In `ai-status`

No current task in `ai-status.json` declares `depends_on: ["LIN-002"]`.

### 1.4 Contract Consumers And Reuse Paths

These are not hard `depends_on` edges in `ai-status`, but they are explicitly named in `services/registry/lineage/read_model_contract.md §10` as consumers of the same lineage read-model contract that `LIN-002` optimizes.

| Consumer | Task ID | Status | Relevance |
|---|---|---|---|
| Incident/postmortem evidence attachment | `INC-001` | review_approved | Uses the lineage read model for evidence-linked incident/postmortem navigation |
| Evidence-linked EvolutionDecision | `EVO-003` | review | Reuses lineage evidence semantics for governed decision links |
| Governed BFF lineage surfaces | `APP-001` | done | Locked the operator/BFF surface assumptions that rely on this lineage projection shape |
| Operator-facing deployment/incident/evolution surfaces | `APP-002` | todo | Will likely consume the optimized lineage read path indirectly through the already-governed BFF surface |

### 1.5 L1 And Contract Sources Referenced

| Document | Section Used |
|---|---|
| `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` | LIN-001 semantic edge and traceability rules |
| `services/registry/lineage/read_model_contract.md` | §8 Query Families And SLA Mapping, §9 Derived-Only Invariants, §10 Downstream Consumers |
| `services/registry/lineage/review_lin001_claude_approved_zh.md` | Approved follow-up expectation that `LIN-002` assembles `upstream_chain[]` / `downstream_chain[]` |

---

## 2. Acceptance Checklist

The parent task acceptance criteria are:

1. `benchmark corpus and p95 target are documented and validated`
2. `deep graph traversal avoids recursive timeout failure`

This packet expands them into a reviewable checklist.

### 2.1 Benchmark Corpus And SLA Contract

| # | Check | Status | Evidence |
|---|---|---|---|
| B1 | The benchmark corpus exists as a machine-readable fixture | ✅ PASS | `services/registry/lineage/lin001a_benchmark_corpus.json` |
| B2 | The corpus metadata is explicitly scoped to `LIN-001A` lineage fixtures | ✅ PASS | `metadata.task_id = "LIN-001A"` |
| B3 | Four query families are defined with stable names and SLA buckets | ✅ PASS | Corpus + `read_model_contract.md §8` |
| B4 | Five benchmark cases exist and define `expected_ids` / marker validation | ✅ PASS | Corpus `benchmark_cases[]` contains 5 cases |
| B5 | The benchmark runner validates expected IDs before timing loops | ✅ PASS | `benchmark.py` runs `validate_case()` before warmup/measurement |
| B6 | Budget enforcement is executable and returns non-zero on violation | ✅ PASS | `benchmark.py --enforce-budgets` checks `budget_failures` |
| B7 | Independent rerun from repo root passes all family budgets | ✅ PASS | `python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets` on 2026-04-10T13:57Z |

### 2.2 Observed Benchmark Results

Independent rerun from this sidecar session:

| Query Family | p95 Target (ms) | Observed p95 (ms) | Result |
|---|---|---|---|
| `runtime_binding_projection` | 500 | 0.037877 | ✅ PASS |
| `capital_pool_projection` | 500 | 0.149139 | ✅ PASS |
| `telemetry_event_trace` | 500 | 0.034921 | ✅ PASS |
| `forensic_plan_trace` | 5000 | 0.009202 | ✅ PASS |

Worst observed p95 in this rerun is `0.149139 ms`, which remains far below the synchronous `500 ms` budget.

### 2.3 Iterative Traversal And Timeout Avoidance

| # | Check | Status | Evidence |
|---|---|---|---|
| T1 | Service is explicitly designed to avoid recursive SQL join timeout failure | ✅ PASS | Module docstring in `services/telemetry/lineage_read/service.py` |
| T2 | Graph store maintains forward and reverse adjacency indexes for O(1) traversal lookup | ✅ PASS | `LineageGraph.forward` and `LineageGraph.reverse` |
| T3 | Traversal uses iterative deque-based BFS with visited-set dedupe | ✅ PASS | `LineageTraverser._bfs_outgoing()` and `_bfs_incoming()` |
| T4 | Traversal has depth and max-visit safety bounds | ✅ PASS | `upstream_depth`, `downstream_depth`, `max_visited` parameters |
| T5 | Upstream/downstream semantics match the normalized dependent→dependency edge direction | ✅ PASS | `traverse_from()` comments and calls: outgoing = upstream, incoming = downstream |
| T6 | Projection payloads are assembled from indexed graph lookups, not recursive joins | ✅ PASS | `ProjectionBuilder` methods and enrich helpers |

### 2.4 Parent Handoff Fixes Confirmed

These are the three implementation points called out in the parent owner's handoff to Claude.

| # | Fix | Status | Evidence |
|---|---|---|---|
| F1 | BFS direction semantics were corrected to match dependent→dependency edges | ✅ PASS | `LineageTraverser.traverse_from()` uses outgoing for upstream and incoming for downstream |
| F2 | `forensic_plan_trace` includes `rollback_parent` runtime bindings as explicit chain items | ✅ PASS | `_enrich_forensic_plan_trace()` appends rollback target runtime bindings when reachable |
| F3 | `runtime_ref` nodes are emitted for visited runtime bindings | ✅ PASS | `_enrich_runtime_binding_projection()`, `_enrich_capital_pool_projection()`, `_enrich_telemetry_event_trace()`, `_enrich_forensic_plan_trace()` |

### 2.5 Query-Family Coverage

| # | Check | Status | Evidence |
|---|---|---|---|
| Q1 | `runtime_binding_projection` returns chain payload plus `telemetry_event_count` / `binding_status` | ✅ PASS | `_enrich_runtime_binding_projection()` |
| Q2 | `capital_pool_projection` returns pool-scoped summary and single-runtime conflict markers | ✅ PASS | `_enrich_capital_pool_projection()` |
| Q3 | `telemetry_event_trace` returns normalized event trace plus artifact/runtime refs | ✅ PASS | `_enrich_telemetry_event_trace()` |
| Q4 | `forensic_plan_trace` returns rollback-aware full plan trace | ✅ PASS | `ProjectionBuilder.forensic_plan_trace()` + `_enrich_forensic_plan_trace()` |
| Q5 | Service query interface rejects unknown families and missing parameters | ✅ PASS | `LineageReadService.query()` negative tests |

### 2.6 Test Coverage

| # | Check | Status | Evidence |
|---|---|---|---|
| V1 | Unit test suite passes from repo root | ✅ PASS | `python3 -m unittest services/telemetry/lineage_read/test_service.py` |
| V2 | 22 tests cover graph store, corpus loader, traverser, four projection builders, service API, and benchmark validation | ✅ PASS | `test_service.py` |
| V3 | Real LIN-001A corpus is loaded in tests, not only a toy fixture | ✅ PASS | `TestLineageReadService.test_load_real_corpus()` |
| V4 | Benchmark cases validate expected IDs and expected marker IDs | ✅ PASS | `TestBenchmarkValidation.test_all_benchmark_cases_pass()` |
| V5 | Missing-node and rollback-marker negative paths are covered | ✅ PASS | `test_traverse_from_missing_node()` and `test_rollback_conflict_detection()` |

### 2.7 Derived-Only Contract Alignment

| # | Check | Status | Evidence |
|---|---|---|---|
| D1 | LIN-002 optimizes the existing LIN-001 query-family split instead of inventing a new surface | ✅ PASS | `read_model_contract.md §8` and §10 |
| D2 | Projection output carries `projection_updated_at` freshness metadata | ✅ PASS | `ProjectionResult.projection_updated_at` and enrich payloads |
| D3 | Conflict markers are surfaced instead of mutating owner truth | ✅ PASS | rollback and single-runtime markers are returned in `conflict_markers[]` |
| D4 | Read path remains an acceleration layer only | ✅ PASS | No owner-write code exists in `services/telemetry/lineage_read/` |

### 2.8 Non-Blocking Follow-Up Status

| # | Item | Status | Note |
|---|---|---|---|
| N1 | Bare `artifact_id` query path | OPEN / NON-BLOCKING | Still listed in `LIN-001` review as optional future enhancement; not part of LIN-002 acceptance |
| N2 | Production storage adapter benchmarking | OPEN / NON-BLOCKING | `benchmark.py` explicitly documents current measurements as in-memory traversal only |

---

## 3. Readiness Verdict

**This sidecar found no acceptance blocker for `LIN-002`.**

What is true right now:

- the benchmark corpus, query-family split, and p95 targets are documented and executable
- the service uses iterative BFS over indexed graph edges rather than recursive joins
- all four query families pass their configured SLA budgets in an independent rerun
- the parent handoff claims about directionality, rollback chain enrichment, and `runtime_ref` emission are all reflected in code and tests

What is not true yet:

- `LIN-002` is **not** ready for `done` from this sidecar alone, because the parent task is still in `review` and awaits Claude's formal review outcome

**Sidecar recommendation**: keep the parent task in `review`, use this packet as reviewer support, and if Claude approves the parent implementation there is no sidecar-discovered acceptance gap left to resolve.

---

## 4. Reproducible Verification

Run these commands from the repo root:

```bash
python3 -m unittest services/telemetry/lineage_read/test_service.py
python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets
```

Expected results:

- unit tests: `22 tests` and `OK`
- benchmark validation: all `5` cases validate and all `4` family reports show `"within_budget": true`

---

## 5. Files Referenced

### Canonical / Contract Sources (read-only)
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`
- `services/registry/lineage/read_model_contract.md`
- `services/registry/lineage/review_lin001_claude_approved_zh.md`

### Parent Implementation Evidence (read-only)
- `services/telemetry/lineage_read/service.py`
- `services/telemetry/lineage_read/test_service.py`
- `services/telemetry/lineage_read/benchmark.py`
- `services/registry/lineage/lin001a_benchmark_corpus.json`

### Shared State Sources
- `ai-status.json`
- `current-work.md`
- `ai-activity-log.jsonl`

### This Sidecar
- `support/sidecars/LIN-002/LIN-002-SIDECAR-ACCEPTANCE.md`

---

## 6. Handoff To Reviewer (Qwen)

Qwen, this packet is ready for review and parent-owner reuse.

It confirms three things:

1. the current `LIN-002` implementation satisfies both declared acceptance criteria with independently rerun evidence
2. the parent handoff's three claimed fixes are all observable in code and tests
3. there is no sidecar-discovered blocker beyond the already-expected parent review with Claude

Recommended next step:

- review this packet
- keep `LIN-002` with Claude for formal review
- if Claude approves, treat this sidecar as complete reviewer support and absorb any useful wording into the parent finalize/handoff path

---

*Generated by Codex as a sidecar acceptance_packet helper for LIN-002. This file is a support artifact and does not modify canonical truth.*

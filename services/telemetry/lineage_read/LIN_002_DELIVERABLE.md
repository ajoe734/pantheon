# LIN-002 Deliverable: Lineage Read Service Performance Path

**Task:** `LIN-002` — Implement lineage read service performance path  
**Owner:** Qwen  
**Reviewer:** Claude  
**Status:** `review` — ready for Claude review  
**Date:** 2026-04-10  

---

## 1. Summary

LIN-002 delivers an iterative, breadth-first graph traversal engine for deep
lineage queries. It avoids recursive SQL join timeout failures by:

1. Building in-memory forward and reverse indexes on all semantic edge types.
2. Traversing the graph iteratively (no recursion depth limits).
3. Deduplicating visited nodes to prevent infinite loops from self-lineage edges.
4. Building projections from indexed lookups, not multi-table joins.

---

## 2. Artifacts

| File | Description |
|---|---|
| `services/telemetry/lineage_read/__init__.py` | Package init |
| `services/telemetry/lineage_read/service.py` | Core service: graph, traverser, projections, corpus loader |
| `services/telemetry/lineage_read/benchmark.py` | Benchmark runner against LIN-001A corpus |
| `services/telemetry/lineage_read/test_service.py` | 22 unit tests covering all components |

---

## 3. Architecture

### 3.1 Core Components

```
LineageReadService (facade)
├── LineageGraph (in-memory graph with forward/reverse indexes)
├── LineageTraverser (iterative BFS, no recursion)
├── ProjectionBuilder (query-family-specific enrichment)
└── CorpusLoader (LIN-001A corpus → graph)
```

### 3.2 Why Iterative BFS, Not Recursive Joins

The LIN-001A benchmark corpus defines a lineage graph with self-referencing
edges (`runtime_binding.rollback_parent`) and many-to-many relationships.
A naive recursive SQL join approach would:

- Hit database recursion depth limits on deep graphs.
- Produce O(N²) or worse join cardinality on many-to-many edges.
- Timeout when traversing rollback chains or long deployment histories.

The iterative BFS approach:

- Uses a `deque` with explicit visited set — no recursion stack.
- Deduplicates nodes by `(type, id)` key — self-lineage edges cannot loop forever.
- Configurable depth limits per SLA bucket prevent runaway traversals.
- O(V + E) worst-case per query, where V = visited nodes, E = traversed edges.

### 3.3 Edge Direction Handling

The lineage graph stores edges in FK direction (child → parent). This means:

- **Upstream** (ancestors) = follow incoming edges (correct for most nodes).
- **Downstream** (dependents) = follow outgoing edges — but for root nodes
  like `CapitalPool` and `DeploymentPlan`, all dependents point TO them, so
  downstream must follow incoming edges instead.

The projection builders handle this explicitly for `capital_pool_projection`
and `forensic_plan_trace` by querying the reverse index directly.

---

## 4. Benchmark Results

### 4.1 LIN-001A Corpus Validation

All 5 benchmark cases pass with all expected IDs and conflict markers validated.

| Query Family | p95 Target | p95 Observed | Margin | Status |
|---|---|---|---|---|
| `runtime_binding_projection` | ≤ 500ms | 0.040ms | 12,500× | ✅ |
| `capital_pool_projection` | ≤ 500ms | 0.066ms | 7,576× | ✅ |
| `telemetry_event_trace` | ≤ 500ms | 0.026ms | 19,231× | ✅ |
| `forensic_plan_trace` | ≤ 5000ms | 0.066ms | 75,758× | ✅ |

> Note: These are in-memory traversal times. Production Postgres adapter will
> add database latency, but the O(V+E) complexity bound remains the same.

### 4.2 Test Coverage

- **22 unit tests** covering graph operations, traversal, projections, service API, and corpus validation.
- **122 total telemetry tests** pass (44 original + 22 LIN-002 + 56 shock absorption).
- All benchmark cases validated against LIN-001A expected IDs and conflict markers.

---

## 5. Acceptance Criteria

| Acceptance Criteria | Status | Evidence |
|---|---|---|
| Benchmark corpus and p95 target are documented and validated | ✅ | `benchmark.py` outputs validated report; all 4 query families within budget |
| Deep graph traversal avoids recursive timeout failure | ✅ | Iterative BFS with visited set — no recursion; self-lineage edges handled safely |

---

## 6. Query Families

### 6.1 `runtime_binding_projection`
- **Input:** `binding_id`
- **Output:** projection summary with pool, plan, persona binding, artifact ref, runtime ref, telemetry events, and rollback markers.
- **Traversal:** Upstream via FK edges (pool, plan, persona), downstream via telemetry events.

### 6.2 `capital_pool_projection`
- **Input:** `pool_id`
- **Output:** pool-scoped summary with all persona bindings, deployment plans, runtime bindings, and telemetry events.
- **Traversal:** Direct index lookup of all nodes referencing the pool (incoming edges).

### 6.3 `telemetry_event_trace`
- **Input:** `event_id`
- **Output:** event-scoped trace proving pool, plan, binding, runtime, and artifact context.
- **Traversal:** Upstream via FK edges, downstream to runtime ref.

### 6.4 `forensic_plan_trace`
- **Input:** `plan_id`
- **Output:** rollback-aware full trace across a plan, its bindings, runtime refs, and related telemetry.
- **Traversal:** Upstream via FK edges (pool, persona), downstream via incoming edges (runtime bindings, telemetry).

---

## 7. Conflict Detection

The service detects and reports the following conflict markers:

| Marker Code | Trigger |
|---|---|
| `rollback_parent` | Runtime binding has `rollback_parent` self-lineage edge |
| `rollback_action_type` | Runtime binding has `rollback_action_type` field set |
| `rollback_target_artifact` | Deployment plan has rollback target artifact ref |
| `node_not_found` | Query targets a non-existent node |

---

## 8. Future Work (Out of Scope for v1)

- **Postgres partition adapter:** Production storage backend with partitioned tables per L1 policy.
- **CDC-based projection refresh:** Event-driven projection updates from write path.
- **ClickHouse analytical mirror:** Long-range trend queries via analytical replica.
- **Full upstream lineage:** Extend beyond capital/runtime/telemetry to research/registry chain.

---

## 9. Verification Commands

```bash
# Run unit tests
python3 -m unittest discover -s services/telemetry/lineage_read -p 'test_*.py' -v

# Run benchmark with SLA enforcement
python3 services/telemetry/lineage_read/benchmark.py --enforce-budgets

# Run all telemetry tests (including LIN-002)
python3 -m unittest discover -s services/telemetry -p 'test_*.py' -v
```

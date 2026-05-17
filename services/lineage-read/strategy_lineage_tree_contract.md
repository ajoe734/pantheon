# Strategy Lineage Tree — Service Contract (STRAT-V2-002)

Status: delivered
Task-ID: STRAT-V2-002
Owner: Claude2
Reviewer: Codex

## Summary

Adds `get_tree(strategy_spec_id, depth)` to the lineage-read service.
Given a StrategySpec ID the API returns a complete, depth-bounded lineage tree
covering all six Pantheon node types:

```
source_record → strategy_spec
  └── experiment_runs
        └── candidate_artifacts
              └── deployment_plans
                    └── runtime_bindings
```

This is an **independent module** (`strategy_lineage_tree.py`).  It does not
import or modify the LIN-001 read-model (`services/telemetry/lineage_read/`).

---

## Public API

### `get_tree(strategy_spec_id, depth, *, store)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `strategy_spec_id` | `str` | required | ID of the root StrategySpec |
| `depth` | `int` | `10` | Max traversal layers below strategy_spec (see table below) |
| `store` | `StrategyLineageStore \| None` | `None` | Injectable store override; defaults to module-level store |

**Returns:** `dict` — never raises an exception.

On success:

```json
{
  "status": 200,
  "tree": { ... }
}
```

When the StrategySpec ID is unknown:

```json
{
  "status": 404,
  "error": "NOT_FOUND",
  "strategy_spec_id": "<id>"
}
```

### Depth semantics

| `depth` | Layers included |
|---------|----------------|
| 0 | `strategy_spec` only |
| 1 | + `source_record` (upstream) |
| 2 | + `experiment_runs` |
| 3 | + `candidate_artifacts` |
| 4 | + `deployment_plans` |
| ≥ 5 | + `runtime_bindings` (full chain) |

---

## Node Shape

Every node in the tree shares the same canonical shape:

```json
{
  "id": "<node_id>",
  "artifact_type": "<node_type>",
  "lineage_refs": {
    "<field_name>": "<ref_id>"
  },
  "created_at": "<ISO-8601>"
}
```

`lineage_refs` contains all cross-node foreign key fields present in the source
data (e.g. `source_id`, `strategy_id`, `run_id`, `artifact_id`, `plan_id`,
`binding_id`).  Fields with `null` values are omitted.

### Node types

| `artifact_type` | Position in tree | Key foreign keys in `lineage_refs` |
|----------------|-----------------|-------------------------------------|
| `source_record` | root upstream | — |
| `strategy_spec` | root | `source_id` |
| `experiment_run` | under `strategy_spec` | `strategy_id` |
| `candidate_artifact` | under `experiment_run` | `run_id` |
| `deployment_plan` | under `candidate_artifact` | `artifact_id` |
| `runtime_binding` | under `deployment_plan` | `plan_id` |

---

## StrategyLineageStore

The `StrategyLineageStore` class is a standalone in-memory indexed store.

### Methods

| Method | Description |
|--------|-------------|
| `add_node(node_type, node_id, data)` | Add a node to the store |
| `get_node(node_type, node_id)` | Retrieve a node by type + ID |
| `find_by_field(node_type, field, value)` | Find all nodes of a type where `data[field] == value` |
| `load_corpus(corpus)` | Bulk-load from LIN-001A-compatible corpus dict |

`add_node` preserves the canonical `node_id` supplied by the caller/loader as
the output `id`, even when the source payload also contains a generic row-level
`id` field.

The corpus format expected by `load_corpus` uses `node_sets` with keys
`source_records`, `strategy_specs`, `experiment_runs`, `candidate_artifacts`,
`deployment_plans`, `runtime_bindings`, matching the LIN-001A benchmark corpus
structure.

---

## Independence Constraints

- Does **not** import from `services.telemetry.lineage_read`.
- Does **not** modify any LIN-001 public APIs or data structures.
- Does **not** write to any persistent store; read-only traversal only.
- Returns a `dict` for all outcomes; never raises to callers.

---

## Verification

```
pytest services/lineage-read/test_strategy_lineage_tree.py -q
13 passed
```

Test coverage:
- full 6-node chain traversal
- 404-equivalent dict for unknown strategy_spec_id
- depth 0–4 limiting (each boundary)
- canonical node shape on all 6 node types
- `lineage_refs` population for all cross-node refs
- multiple experiment runs under one strategy_spec
- missing source_record does not raise
- `load_corpus` bulk-load round-trip
- `load_corpus` preserves domain IDs when generic row IDs are also present

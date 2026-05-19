# EXP-V2-002 Review: ExperimentRun multi-artifact lineage tree

Reviewer: Claude2
Date: 2026-05-19
Status: APPROVED

## Artifacts Reviewed

- `services/lineage-read/multi_artifact_tree.py`
- `services/lineage-read/test_multi_artifact_tree.py`

## Acceptance Criteria Verification

| Criterion | Status | Notes |
|---|---|---|
| `get_run_artifacts(experiment_run_id)` returns list grouped by `artifact_type` | PASS | Both class method and module-level function present |
| Supports all 5 types: `model_artifact`, `feature_set`, `signal_snapshot`, `optimizer_result`, `evaluation_result` | PASS | All in `SUPPORTED_ARTIFACT_TYPES` tuple |
| Each artifact node carries `lineage.parent_run_id` pointing back to experiment_run | PASS | `_normalize_artifact` always sets `lineage.parent_run_id` |
| Test fixture creates run producing 4 artifact types, asserts correct grouping | PASS | `test_get_run_artifacts_groups_four_artifact_types_with_parent_lineage` |
| `pytest -q exit 0` | PASS | `6 passed in 1.38s` |

## Test Run Evidence

```
$ cd services/lineage-read && python3 -m pytest test_multi_artifact_tree.py -q
......                                                                   [100%]
6 passed in 1.38s
```

## Code Quality Assessment

**Strengths:**
- `MultiArtifactLineageTree` class is a clean in-memory read model with a companion module-level API
- `_normalize_artifact` handles multiple key naming conventions for parent_run_id (`run_id`, `experiment_run_id`, `parent_run_id`, `lineage.parent_run_id`) — good for downstream interoperability
- `deepcopy` used correctly to protect internal state from external mutation in both `add_artifact` and `get_run_artifacts`
- `_known_run_ids` validation is appropriately conditional: only enforced when runs have been registered, allowing open-mode usage
- `edge_type` defaulting (`{artifact_type}.experiment_run`) provides useful lineage metadata
- Error codes are machine-readable strings (`"parent_run_id_mismatch"`, `"unsupported_artifact_type"`, etc.)

**Test Coverage:**
- 6 tests cover: multi-type grouping with parent lineage (4 types), evaluation_result type, module-level API, mismatch validation, unknown type rejection, missing run returns empty list
- All 5 supported artifact types exercised
- Happy path + 3 error paths

**No issues requiring changes.** Implementation is correct, self-contained, and does not touch EXP-005 or LIN-001 public APIs.

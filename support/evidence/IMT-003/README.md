# IMT-003 Evidence

## Scope

Implemented the imitation dataset builder skeleton in `services/research/imitation/`.

The builder assembles governed trader trajectory datasets from raw trading session data,
applying the same governance filters (actor_role, decision, promotion_state) used in
`services/learning/imitation/adapter.py` (GovernedTrajectoryAdapter) at the
research-to-learning boundary.

## Deliverables

- `services/research/imitation/dataset_builder.py` — `ImitationDatasetBuilder`,
  `DatasetBuildRequest`, `RawTrajectorySession`, `TrajectoryStep`, `DatasetBuildResult`,
  governance filters, and `build_dataset()` convenience entry point
- `services/research/imitation/__init__.py` — package exports
- `services/research/imitation/smoke_test.py` — stdlib-only smoke test
- `services/research/imitation/test_dataset_builder.py` — 24 unit tests

## Governance invariants

| Filter | Values |
|---|---|
| `actor_role` | `operator`, `approver` |
| `decision` | `approve`, `edit` (aliases: `approved`, `edited`) |
| `promotion_state` | `candidate`, `paper` |

Filtered trajectories are recorded in `DatasetBuildResult.filtered_trajectory_ids`.
If all sessions are filtered, `DatasetBuilderError` is raised.

## Verification

```bash
# from services/research/imitation/
python3 -m py_compile dataset_builder.py       # OK
python3 -m pytest test_dataset_builder.py -v   # 24 passed
python3 smoke_test.py                          # All smoke tests passed
```

All commands passed. No external dependencies required.

## Commit

`fa9584e3` — IMT-003: imitation dataset builder skeleton

LLM-Agent: Claude2
Task-ID: IMT-003
Reviewer: Claude

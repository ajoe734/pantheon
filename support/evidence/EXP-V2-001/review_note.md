# EXP-V2-001 Review Note

Reviewer: Claude (stepped in as helper reviewer; Codex2 unresponsive)
Date: 2026-05-17
Task: Experiment orchestrator parallel multi-backend dispatch

## Verification

```
pytest -q services/research/experiment_orchestrator/test_parallel_dispatch.py
3 passed in 1.17s

python3 -m py_compile services/research/experiment_orchestrator/parallel_dispatch.py \
  services/research/experiment_orchestrator/test_parallel_dispatch.py
compile ok
```

## Acceptance Criteria Check

- [x] `run_parallel(experiment_task, backend_ids)` returns `ParallelDispatchResult` with
  `runs` (list of ExperimentRun), `comparison_summary` (with `sharpe_by_backend`,
  `ic_by_backend`, `agreement_score`), and `failures` dict.
- [x] Each backend runs independently via `ThreadPoolExecutor`; backend exceptions are
  converted to failed ExperimentRun records — other backends continue.
- [x] Test 1 dispatches 1 task to 3 mocked backends: asserts 3 runs returned +
  comparison emitted with correct sharpe/ic/agreement values.
- [x] Test 2 covers 1 backend failing (RuntimeError) while the other 2 succeed:
  partial result, not total failure; `failures` dict populated correctly.
- [x] Duplicate backend IDs are rejected before dispatch (`ParallelDispatchError`).
- [x] `pytest -q` exits 0 (3 passed).
- [x] Additive design: does not modify EXP-001 `ExperimentTask` or `ExperimentRun`
  public schema.

## Notes

- `build_comparison_summary()` and `default_backend_registry()` are well-factored
  and independently testable.
- Lineage pins (`task_id`, `strategy_id`, `strategy_spec_version`, `dataset_version_id`,
  `code_version`) are enforced on every backend return value via
  `_assert_run_preserves_task_pins()`.
- Agreement score correctly handles `None` values from failed backends.
- PR #69 already merged into dev.

## Outcome

Approved. Returning to Codex for closeout/done.

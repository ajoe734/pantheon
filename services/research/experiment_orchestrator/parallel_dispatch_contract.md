# Parallel Dispatch Contract

Task: `EXP-V2-001`

## Scope

`services/research/experiment_orchestrator/parallel_dispatch.py` adds an
additive fanout helper for the Experiment Orchestrator. It does not change the
EXP-001 `ExperimentTask` or `ExperimentRun` schema.

## API

```python
run_parallel(experiment_task, backend_ids, *, backend_registry=None, max_workers=None)
```

Inputs:

- `experiment_task`: an `ExperimentTask` instance or schema-valid mapping.
- `backend_ids`: non-empty unique backend ids, for example `vectorbt`, `qlib`,
  and `statsmodels`.
- `backend_registry`: optional mapping from backend id to a callable. The
  callable may accept `(experiment_task, backend_id)` or only
  `(experiment_task)`, and must return an `ExperimentRun` or schema-valid
  `ExperimentRun` mapping.

Output:

- `ParallelDispatchResult.runs`: one `ExperimentRun` per requested backend, in
  the same order as `backend_ids`.
- `ParallelDispatchResult.comparison_summary`: a dict with
  `sharpe_by_backend`, `ic_by_backend`, `agreement_score`,
  `status_by_backend`, `run_id_by_backend`, `successful_backends`,
  `failed_backends`, and `compared_backend_count`.
- `ParallelDispatchResult.failures`: backend id to failure reason for failed
  backend runs.

## Failure Semantics

Each backend is isolated. If one backend raises or returns a result that does
not preserve the parent task lineage pins, `run_parallel` converts that backend
result into a schema-valid failed `ExperimentRun`. Other backends continue and
their completed runs are returned.

The failed run uses:

- `status="failed"`
- `runtime_env="research"`
- parent task `task_id`, `strategy_id`, `strategy_spec_version`,
  `dataset_version_id`, and `code_version`
- `failure_reason` with the backend error

## Comparison Semantics

The comparison summary is computed from `ExperimentRun.metadata` only. The
extractor recognizes:

- Sharpe: `sharpe`, `sharpe_ratio`, `mean_sharpe_ratio`
- IC: `ic`, `information_coefficient`, `mean_ic`

The `agreement_score` is the mean pairwise sign agreement across available
Sharpe and IC values. It is `None` when fewer than two comparable metric values
exist.

## Default Backend Aliases

The default registry provides CI-safe lazy adapters for:

- `vectorbt`, `vectorbt_portfolio`
- `qlib`, `qlib_rolling_oos`
- `statsmodels`

Default adapters read governed backend input from
`ExperimentTask.metadata.backend_inputs`. Tests can pass a mock
`backend_registry` to verify orchestration behavior without importing upstream
frameworks.

